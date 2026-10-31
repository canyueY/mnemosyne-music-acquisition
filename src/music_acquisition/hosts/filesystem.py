# -*- coding: utf-8 -*-
"""Filesystem HostRuntime for Mnemosyne Node Mode B."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class FileCredentialStore:
    def __init__(self, root: Path) -> None:
        self._root = root / "credentials"
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("/", "_").replace(":", "_")
        return self._root / f"{safe}.bin"

    async def save(self, key: str, value: bytes) -> None:
        self._path(key).write_bytes(value)

    async def load(self, key: str) -> bytes | None:
        p = self._path(key)
        return p.read_bytes() if p.is_file() else None

    async def delete(self, key: str) -> None:
        p = self._path(key)
        if p.is_file():
            p.unlink()

    async def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class FileLocalStorage:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def root(self) -> str:
        return str(self._root)

    def path(self, *parts: str) -> str:
        p = self._root.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)

    def write_bytes(self, rel: str, data: bytes) -> None:
        Path(self.path(rel)).write_bytes(data)

    def read_bytes(self, rel: str) -> bytes | None:
        p = self._root / rel
        return p.read_bytes() if p.is_file() else None

    def write_json(self, rel: str, obj: Any) -> None:
        self.write_bytes(rel, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))

    def read_json(self, rel: str) -> Any | None:
        raw = self.read_bytes(rel)
        return json.loads(raw.decode("utf-8")) if raw else None


class _Scheduler:
    def schedule(self, job_id: str, delay_sec: float, callback: Any) -> str:
        return job_id

    def cancel(self, handle: str) -> None:
        return None


class _Events:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        self.events.append((topic, payload))


class _Http:
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout_sec: float = 30.0,
    ) -> tuple[int, bytes, dict[str, str]]:
        import urllib.request

        req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            return resp.status, resp.read(), dict(resp.headers.items())


class _UI:
    def __init__(self, root: Path) -> None:
        self._root = root
        self.last_qr: dict[str, str] = {}

    async def show_qr(self, session_id: str, image_or_url: str, message: str = "") -> None:
        self.last_qr = {"session_id": session_id, "url": image_or_url, "message": message}
        meta = {
            "session_id": session_id,
            "qr_url": image_or_url,
            "message": message,
        }
        path = self._root / "auth" / f"qr_{session_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        # best-effort PNG for human scan
        try:
            from music_acquisition.source.netease.qr_auth import save_qr_png

            png = self._root / "auth" / f"qr_{session_id}.png"
            save_qr_png(image_or_url, str(png))
            meta["png"] = str(png)
            path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    async def notify(self, level: str, message: str) -> None:
        return None


class _Logger:
    def info(self, msg: str, **fields: Any) -> None:
        print(f"[music-acq] INFO {msg} {fields}", flush=True)

    def warning(self, msg: str, **fields: Any) -> None:
        print(f"[music-acq] WARN {msg} {fields}", flush=True)

    def error(self, msg: str, **fields: Any) -> None:
        print(f"[music-acq] ERROR {msg} {fields}", flush=True)


class _Clock:
    def __init__(self, tz: str = "Asia/Shanghai") -> None:
        self._tz = tz

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def timezone(self) -> str:
        return self._tz


class FilesystemHostRuntime:
    """Mode B host: data under <node_data>/music-acquisition/."""

    def __init__(self, root: str | Path, *, timezone: str = "Asia/Shanghai") -> None:
        base = Path(root)
        base.mkdir(parents=True, exist_ok=True)
        self.credential_store = FileCredentialStore(base)
        self.task_scheduler = _Scheduler()
        self.event_publisher = _Events()
        self.local_storage = FileLocalStorage(base)
        self.http_transport = _Http()
        self.user_interaction = _UI(base)
        self.logger = _Logger()
        self.clock = _Clock(timezone)
        self.host_name = "mnemosyne_node_fs"
