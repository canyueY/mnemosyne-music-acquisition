# -*- coding: utf-8 -*-
"""Persist playlist boundary scopes into Mnemosyne Memory Bank.

源歌单（source:*）与运行时队列（runtime:*）分文件存放，支持：
* track ↔ (scope, index) 往返查找
* 打开某一歌单时仍可读其它已登记源歌单
* 与桌宠 ``Murasame.playlist_scope`` 进程内表双向同步（软依赖）
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from music_acquisition.bank_paths import playlist_scopes_dir

SCOPE_DAILY = "source:daily_recommend"
SCOPE_QUEUE = "runtime:playing_queue"
# Windows 文件名禁止 `:`（否则会变成 NTFS ADS），故把分隔符压成 `__`
_SAFE_KEY = re.compile(r"[^a-zA-Z0-9._+-]+")


def _safe_filename(key: str) -> str:
    raw = str(key or "scope").replace(":", "__")
    return _SAFE_KEY.sub("_", raw)[:180] or "scope"


class PlaylistScopeStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root else playlist_scopes_dir(create=True)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._meta_path = self._root / "_meta.json"

    @property
    def root(self) -> Path:
        return self._root

    def _scope_path(self, key: str) -> Path:
        return self._root / f"{_safe_filename(key)}.json"

    def _read_meta(self) -> dict[str, Any]:
        if not self._meta_path.is_file():
            return {"viewed_key": "", "active_source_key": "", "updated_at": 0.0}
        try:
            raw = json.loads(self._meta_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {"viewed_key": "", "active_source_key": "", "updated_at": 0.0}

    def _write_meta(self, meta: dict[str, Any]) -> None:
        meta = dict(meta)
        meta["updated_at"] = time.time()
        self._meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def upsert(
        self,
        key: str,
        *,
        name: str,
        tracks: list[dict[str, Any]],
        kind: str = "source",
        provider_playlist_id: str = "",
        scene: str = "",
        activate: bool = False,
        viewed: bool = False,
        mirror_to_client: bool = True,
    ) -> dict[str, Any]:
        clean_key = str(key or "").strip()
        if not clean_key:
            raise ValueError("scope key required")
        rows: list[dict[str, Any]] = []
        for raw in tracks or []:
            if not isinstance(raw, dict):
                continue
            tid = str(
                raw.get("track_id")
                or raw.get("id")
                or raw.get("song_id")
                or raw.get("provider_track_id")
                or ""
            ).strip()
            if not tid:
                continue
            rows.append(
                {
                    "track_id": tid,
                    "title": str(raw.get("title") or raw.get("name") or ""),
                    "artists": str(raw.get("artists") or ""),
                    "index": len(rows),
                    "index_1based": len(rows) + 1,
                }
            )
        payload = {
            "schema": "music.playlist_scope.v1",
            "key": clean_key,
            "kind": kind or ("runtime" if clean_key.startswith("runtime:") else "source"),
            "name": str(name or clean_key),
            "provider_playlist_id": str(provider_playlist_id or ""),
            "scene": str(scene or ""),
            "updated_at": time.time(),
            "total": len(rows),
            "tracks": rows,
        }
        with self._lock:
            self._scope_path(clean_key).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            meta = self._read_meta()
            if activate and clean_key.startswith("source:"):
                meta["active_source_key"] = clean_key
            if viewed:
                meta["viewed_key"] = clean_key
            self._write_meta(meta)
        if mirror_to_client:
            self._mirror_to_murasame(payload, activate=activate, viewed=viewed)
        return payload

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._scope_path(key)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else None
        except Exception:
            return None

    def list_scopes(self, *, include_runtime: bool = True) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with self._lock:
            for path in sorted(self._root.glob("*.json")):
                if path.name.startswith("_"):
                    continue
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(raw, dict):
                    continue
                key = str(raw.get("key") or "")
                if not include_runtime and key.startswith("runtime:"):
                    continue
                rows.append(
                    {
                        "key": key,
                        "kind": raw.get("kind"),
                        "name": raw.get("name"),
                        "total": raw.get("total") or len(raw.get("tracks") or []),
                        "provider_playlist_id": raw.get("provider_playlist_id") or "",
                        "scene": raw.get("scene") or "",
                        "updated_at": raw.get("updated_at") or 0.0,
                    }
                )
        rows.sort(key=lambda r: (-float(r.get("updated_at") or 0.0), str(r.get("key"))))
        return rows

    def lookup_track(
        self, track_id: str, *, include_runtime: bool = False
    ) -> list[dict[str, Any]]:
        tid = str(track_id or "").strip()
        if not tid:
            return []
        hits: list[dict[str, Any]] = []
        for meta in self.list_scopes(include_runtime=include_runtime):
            scope = self.get(str(meta["key"]))
            if not scope:
                continue
            for tr in scope.get("tracks") or []:
                if not isinstance(tr, dict):
                    continue
                if str(tr.get("track_id") or "") != tid:
                    continue
                hits.append(
                    {
                        "scope_key": scope.get("key"),
                        "scope_name": scope.get("name"),
                        "kind": scope.get("kind"),
                        "index": int(tr.get("index") or 0),
                        "index_1based": int(tr.get("index_1based") or (int(tr.get("index") or 0) + 1)),
                        "track_id": tid,
                        "title": tr.get("title") or "",
                        "artists": tr.get("artists") or "",
                        "provider_playlist_id": scope.get("provider_playlist_id") or "",
                        "scene": scope.get("scene") or "",
                    }
                )
                break
        hits.sort(key=lambda h: (0 if str(h.get("scope_key")).startswith("source:") else 1))
        return hits

    def lookup_index(self, scope_key: str, index_1based: int) -> dict[str, Any] | None:
        scope = self.get(scope_key)
        if not scope:
            return None
        try:
            idx = int(index_1based) - 1
        except (TypeError, ValueError):
            return None
        tracks = scope.get("tracks") or []
        if idx < 0 or idx >= len(tracks):
            return None
        tr = tracks[idx]
        if not isinstance(tr, dict):
            return None
        return {
            "scope_key": scope.get("key"),
            "scope_name": scope.get("name"),
            "kind": scope.get("kind"),
            "index": idx,
            "index_1based": idx + 1,
            "track_id": str(tr.get("track_id") or ""),
            "title": tr.get("title") or "",
            "artists": tr.get("artists") or "",
            "total": len(tracks),
        }

    def neighbor(
        self,
        scope_key: str,
        track_id: str,
        *,
        delta: int = 1,
        wrap: bool = True,
    ) -> dict[str, Any] | None:
        scope = self.get(scope_key)
        if not scope:
            return None
        tracks = [t for t in (scope.get("tracks") or []) if isinstance(t, dict)]
        tid = str(track_id or "").strip()
        idx = next((i for i, t in enumerate(tracks) if str(t.get("track_id")) == tid), -1)
        if idx < 0 or not tracks:
            return None
        nxt = idx + int(delta)
        wrapped = False
        if nxt < 0 or nxt >= len(tracks):
            if not wrap:
                return None
            nxt = ((nxt % len(tracks)) + len(tracks)) % len(tracks)
            wrapped = True
        tr = tracks[nxt]
        return {
            "scope_key": scope.get("key"),
            "scope_name": scope.get("name"),
            "kind": scope.get("kind"),
            "track_id": str(tr.get("track_id") or ""),
            "title": tr.get("title") or "",
            "artists": tr.get("artists") or "",
            "index": nxt,
            "index_1based": nxt + 1,
            "from_index": idx,
            "from_index_1based": idx + 1,
            "total": len(tracks),
            "wrapped": wrapped,
        }

    def roundtrip(
        self,
        *,
        track_id: str | None = None,
        scope_key: str | None = None,
        index_1based: int | None = None,
        include_runtime: bool = False,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": False,
            "track_id": str(track_id or "").strip(),
            "scope_key": str(scope_key or "").strip(),
            "index_1based": index_1based,
            "hits": [],
            "resolved": None,
            "bank_root": str(self._root),
        }
        if scope_key and index_1based is not None:
            resolved = self.lookup_index(scope_key, int(index_1based))
            out["resolved"] = resolved
            out["ok"] = bool(resolved)
            if resolved:
                out["track_id"] = str(resolved.get("track_id") or "")
            return out
        if track_id:
            hits = self.lookup_track(track_id, include_runtime=include_runtime)
            out["hits"] = hits
            out["resolved"] = hits[0] if hits else None
            out["ok"] = bool(hits)
            if hits and not out["scope_key"]:
                out["scope_key"] = str(hits[0].get("scope_key") or "")
            return out
        return out

    def resolve_active_source(self) -> str:
        meta = self._read_meta()
        for key in (meta.get("viewed_key"), meta.get("active_source_key")):
            k = str(key or "").strip()
            if k.startswith("source:") and self.get(k):
                return k
        for row in self.list_scopes(include_runtime=False):
            return str(row.get("key") or "")
        return ""

    def snapshot(self) -> dict[str, Any]:
        meta = self._read_meta()
        return {
            "bank_scopes_dir": str(self._root),
            "viewed_key": meta.get("viewed_key") or "",
            "active_source_key": meta.get("active_source_key") or self.resolve_active_source(),
            "scopes": self.list_scopes(include_runtime=True),
        }

    def ingest_from_murasame_registry(self) -> dict[str, Any]:
        """把桌宠进程内登记表刷进 Memory Bank（双向适配入口）。"""
        try:
            from Murasame.playlist_scope import get_registry  # type: ignore
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"murasame_unavailable:{exc}"}
        reg = get_registry()
        snap = reg.snapshot()
        written = 0
        for row in snap.get("scopes") or []:
            key = str(row.get("key") or "")
            scope = reg.get(key)
            if not scope:
                continue
            self.upsert(
                key,
                name=scope.name,
                tracks=[
                    {"track_id": t.track_id, "title": t.title, "artists": t.artists}
                    for t in scope.tracks
                ],
                kind=scope.kind,
                provider_playlist_id=scope.provider_playlist_id,
                scene=scope.scene,
                activate=(key == snap.get("active_source_key")),
                viewed=(key == snap.get("viewed_key")),
                mirror_to_client=False,
            )
            written += 1
        return {"ok": True, "written": written, "snapshot": self.snapshot()}

    def hydrate_murasame_registry(self) -> dict[str, Any]:
        """从 Memory Bank 灌回桌宠进程内表（启动 / 跨进程往返）。"""
        try:
            from Murasame.playlist_scope import get_registry  # type: ignore
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"murasame_unavailable:{exc}"}
        reg = get_registry()
        meta = self._read_meta()
        loaded = 0
        reg.suspend_bank_persist(True)
        try:
            for row in self.list_scopes(include_runtime=True):
                scope = self.get(str(row["key"]))
                if not scope:
                    continue
                key = str(scope.get("key") or "")
                reg.upsert(
                    key,
                    name=str(scope.get("name") or key),
                    tracks=list(scope.get("tracks") or []),
                    kind=str(scope.get("kind") or "source"),
                    provider_playlist_id=str(scope.get("provider_playlist_id") or ""),
                    scene=str(scope.get("scene") or ""),
                    activate=(key == meta.get("active_source_key")),
                    viewed=(key == meta.get("viewed_key")),
                )
                loaded += 1
        finally:
            reg.suspend_bank_persist(False)
        return {"ok": True, "loaded": loaded, "registry": reg.snapshot()}

    def _mirror_to_murasame(
        self, payload: dict[str, Any], *, activate: bool, viewed: bool
    ) -> None:
        try:
            from Murasame.playlist_scope import get_registry  # type: ignore
        except Exception:
            return
        try:
            reg = get_registry()
            reg.suspend_bank_persist(True)
            try:
                reg.upsert(
                    str(payload.get("key") or ""),
                    name=str(payload.get("name") or ""),
                    tracks=list(payload.get("tracks") or []),
                    kind=str(payload.get("kind") or "source"),
                    provider_playlist_id=str(payload.get("provider_playlist_id") or ""),
                    scene=str(payload.get("scene") or ""),
                    activate=activate,
                    viewed=viewed,
                )
            finally:
                reg.suspend_bank_persist(False)
        except Exception:
            return


_STORE: PlaylistScopeStore | None = None
_STORE_LOCK = threading.Lock()


def get_store(root: Path | None = None) -> PlaylistScopeStore:
    global _STORE
    with _STORE_LOCK:
        if root is not None:
            return PlaylistScopeStore(root)
        if _STORE is None:
            _STORE = PlaylistScopeStore()
        return _STORE
