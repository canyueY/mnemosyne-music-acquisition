# -*- coding: utf-8 -*-
"""Optional playback — prefers Murasame bridge when available; else unsupported.

切歌 / 序号点播优先走源歌单边界（Memory Bank playlist_scopes），
避免把 runtime 播放队列下标当成「歌单第 N 首」。
"""

from __future__ import annotations

import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from music_acquisition.playlist_scope_store import PlaylistScopeStore


class MusicPlaybackService:
    def __init__(
        self,
        *,
        enable_murasaame_bridge: bool = True,
        scope_store: "PlaylistScopeStore | None" = None,
        verify_timeout_sec: float = 4.0,
        verify_poll_sec: float = 0.4,
        authorize=None,
        snapshot_resolver=None,
    ) -> None:
        self._bridge = enable_murasaame_bridge
        self._scope_store = scope_store
        self._last: dict[str, Any] = {}
        self._verify_timeout_sec = float(verify_timeout_sec)
        self._verify_poll_sec = float(verify_poll_sec)
        self._authorize = authorize
        self._resolve_snapshot = snapshot_resolver
        self._active_scope = None

    def _play_resolved(self, kind, **target):
        resolved = self._resolve_snapshot(kind, **target)
        if not resolved.get("ok"):
            return resolved
        result = self.play_song(resolved["track_id"])
        result["target"] = resolved
        if result.get("ok"):
            self._active_scope = resolved["scope"]
        return result

    def _allowed(self):
        return self._authorize is None or self._authorize()

    def capabilities(self) -> dict[str, bool]:
        ok = False
        verify = False
        if self._bridge:
            try:
                from Murasame import netease_music as NM  # type: ignore

                ok = bool(getattr(NM, "is_enabled", lambda: True)())
            except Exception:
                ok = False
            try:
                from Murasame import netease_cdp as CDP  # type: ignore

                verify = hasattr(CDP, "cdp_is_playing") and hasattr(CDP, "cdp_current_track_id")
            except Exception:
                verify = False
        return {
            "playbackControl": ok,
            "playSong": ok,
            "playPlaylist": ok,
            "playDaily": ok,
            "playNextInScope": ok,
            "roundtripLookup": self._scope_store is not None or ok,
            "verifyPlayback": bool(ok and verify),
        }

    def _verify_playing(self, expected_track_id: str | None = None) -> dict[str, Any]:
        """Poll CDP until playing (and optional track id match) or timeout."""
        if not self.capabilities().get("verifyPlayback"):
            return {"ok": False, "verified": False, "error": "VERIFY_UNSUPPORTED"}
        try:
            from Murasame.netease_cdp import (  # type: ignore
                cdp_current_track_id,
                cdp_get_playing_track_info,
                cdp_is_playing,
            )
            from Murasame.netease_music import _try_cdp_ready  # type: ignore
            from Murasame.utils import get_config  # type: ignore

            ready, port = _try_cdp_ready(get_config())
            if not ready:
                return {"ok": False, "verified": False, "error": "CDP_NOT_READY"}

            deadline = time.monotonic() + self._verify_timeout_sec
            last: dict[str, Any] = {}
            expect = str(expected_track_id or "").replace("netease:", "").strip()
            while time.monotonic() < deadline:
                playing = bool(cdp_is_playing(port=port))
                cur = str(cdp_current_track_id(port=port) or "")
                info: dict[str, Any] = {}
                try:
                    info = cdp_get_playing_track_info(port=port) or {}
                except Exception:
                    info = {}
                last = {"playing": playing, "track_id": cur, "info": info, "port": port}
                if playing and expect and cur.replace("netease:", "").strip() == expect:
                    return {"ok": True, "verified": True, **last}
                time.sleep(self._verify_poll_sec)
            return {
                "ok": False,
                "verified": False,
                "error": "VERIFY_TIMEOUT",
                **last,
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "verified": False, "error": "VERIFY_FAILED", "detail": str(exc)}

    def play_song(self, track_id: str, *, verify: bool = True) -> dict[str, Any]:
        if not self.capabilities().get("playSong"):
            return {"ok": False, "error": "PLAYBACK_UNSUPPORTED"}
        if not self._allowed():
            return {"ok": False, "error": "PLAYBACK_PERMISSION_DENIED"}
        try:
            from Murasame import netease_music as NM  # type: ignore

            sid = int(str(track_id).replace("netease:", ""))
            result = NM.launch_cloudmusic_song(sid)
            launched = bool(result.get("ok")) if isinstance(result, dict) else bool(result)
            if not launched:
                return {"ok": False, "error": "PLAY_FAILED"}
            out: dict[str, Any] = {"ok": False, "command_sent": True, "track_id": str(sid), "error": "PLAY_VERIFY_UNAVAILABLE"}
            if verify and self.capabilities().get("verifyPlayback"):
                v = self._verify_playing(str(sid))
                out["verify"] = v
                out["ok"] = bool(v.get("verified"))
                if not out["ok"]:
                    out["error"] = v.get("error") or "PLAY_NOT_VERIFIED"
                else:
                    out.pop("error", None)
            self._last = out
            return out
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "PLAY_FAILED", "detail": str(exc)}

    def play_playlist(
        self, playlist_id: str, *, track_id: str | None = None, index: int | None = None
    ) -> dict[str, Any]:
        if track_id is not None and index is not None:
            return {"ok": False, "error": "AMBIGUOUS_TARGET"}
        if not self.capabilities().get("playPlaylist"):
            return {"ok": False, "error": "PLAYBACK_UNSUPPORTED"}
        if not self._allowed():
            return {"ok": False, "error": "PLAYBACK_PERMISSION_DENIED"}
        if self._resolve_snapshot:
            return self._play_resolved("playlist", playlist_id=playlist_id, track_id=track_id, index=index)
        try:
            from Murasame import netease_music as NM  # type: ignore

            pid = int(str(playlist_id).replace("netease:", ""))
            if index is not None and self._scope_store is not None:
                hit = self._scope_store.lookup_index(f"source:playlist:{pid}", int(index))
                if hit and hit.get("track_id"):
                    return self.play_song(str(hit["track_id"]))
            if track_id is not None:
                return self.play_song(track_id)
            result = NM.play_playlist_id(pid)
            out: dict[str, Any] = {"ok": False, "playlist": pid, "command_sent": bool(result), "error": "PLAY_TARGET_UNRESOLVED"}
            if self.capabilities().get("verifyPlayback"):
                v = self._verify_playing(None)
                out["verify"] = v
                # playlist open may not immediately report track — soft verify
                out["ok"] = False  # opening a playlist is not proof of its actual track
            self._last = out
            return out
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "PLAY_FAILED", "detail": str(exc)}

    def play_daily(
        self, date: str, *, track_id: str | None = None, index: int | None = None
    ) -> dict[str, Any]:
        if track_id is not None and index is not None:
            return {"ok": False, "error": "AMBIGUOUS_TARGET"}
        if not self.capabilities().get("playDaily"):
            return {"ok": False, "error": "PLAYBACK_UNSUPPORTED"}
        if not self._allowed():
            return {"ok": False, "error": "PLAYBACK_PERMISSION_DENIED"}
        if self._resolve_snapshot:
            return self._play_resolved("daily", date=date, track_id=track_id, index=index)
        try:
            if index is not None and self._scope_store is not None:
                hit = self._scope_store.lookup_index("source:daily_recommend", int(index))
                if hit and hit.get("track_id"):
                    played = self.play_song(str(hit["track_id"]))
                    played["scope"] = hit
                    played["date"] = date
                    return played
            if track_id is not None:
                return self.play_song(track_id)
            from Murasame import netease_music as NM  # type: ignore

            result = NM.open_daily_recommend_page()
            self._last = {"date": date, "result": result}
            return {"ok": False, "command_sent": bool(result), "error": "PLAY_TARGET_UNRESOLVED", "note": "opens_daily_page"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "PLAY_FAILED", "detail": str(exc)}

    def play_next_in_scope(self, *, delta: int = 1) -> dict[str, Any]:
        """下一首/上一首：锚定源歌单（桌宠 CDP + Bank 边界）。"""
        if not self.capabilities().get("playNextInScope"):
            return {"ok": False, "error": "PLAYBACK_UNSUPPORTED"}
        if not self._allowed():
            return {"ok": False, "error": "PLAYBACK_PERMISSION_DENIED"}
        if self._resolve_snapshot:
            if not self._active_scope:
                return {"ok": False, "error": "PLAY_SOURCE_UNKNOWN"}
            try:
                from Murasame.netease_music import _try_cdp_ready
                from Murasame.netease_cdp import cdp_current_track_id
                from Murasame.utils import get_config
                ready, port = _try_cdp_ready(get_config())
                if not ready:
                    return {"ok": False, "error": "CDP_NOT_READY"}
                current = str(cdp_current_track_id(port=port) or "")
                return self._play_resolved(self._active_scope["kind"], **{k: v for k, v in self._active_scope.items() if k != "kind"}, current_track_id=current, delta=delta)
            except Exception:
                return {"ok": False, "error": "PLAY_TARGET_UNRESOLVED"}
        try:
            from Murasame import netease_music as NM  # type: ignore
            from Murasame.utils import get_config  # type: ignore

            action = "next" if int(delta) >= 0 else "prev"
            result = NM.media_control(action, cfg=get_config())
            if self._scope_store is not None:
                try:
                    self._scope_store.ingest_from_murasame_registry()
                except Exception:
                    pass
            out: dict[str, Any] = {"ok": bool(result.get("ok")), "result": result, "action": action}
            if out["ok"] and self.capabilities().get("verifyPlayback"):
                v = self._verify_playing(None)
                out["verify"] = v
                out["ok"] = False
            out["command_sent"] = bool(result.get("ok"))
            out["ok"] = False
            out["error"] = "PLAY_TARGET_UNRESOLVED"
            self._last = out
            return out
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "CONTROL_FAILED", "detail": str(exc)}

    def roundtrip_lookup(
        self,
        *,
        track_id: str | None = None,
        scope_key: str | None = None,
        index_1based: int | None = None,
        include_runtime: bool = False,
        prefer_client: bool = True,
    ) -> dict[str, Any]:
        """往返查找：优先客户端 CDP 同步，否则读 Memory Bank。"""
        if not self._allowed():
            return {"ok": False, "error": "PLAYBACK_PERMISSION_DENIED"}
        if prefer_client:
            try:
                from Murasame.netease_cdp import cdp_roundtrip_lookup  # type: ignore
                from Murasame.netease_music import _try_cdp_ready  # type: ignore
                from Murasame.utils import get_config  # type: ignore

                ready, port = _try_cdp_ready(get_config())
                if ready:
                    hit = cdp_roundtrip_lookup(
                        track_id=track_id,
                        scope_key=scope_key,
                        index_1based=index_1based,
                        port=port,
                        include_runtime=include_runtime,
                    )
                    if self._scope_store is not None:
                        try:
                            self._scope_store.ingest_from_murasame_registry()
                        except Exception:
                            pass
                    hit["source"] = "client+bank"
                    return hit
            except Exception as exc:  # noqa: BLE001
                client_err = str(exc)
        else:
            client_err = None
        if self._scope_store is None:
            return {"ok": False, "error": "NO_SCOPE_STORE", "client_error": client_err}
        hit = self._scope_store.roundtrip(
            track_id=track_id,
            scope_key=scope_key,
            index_1based=index_1based,
            include_runtime=include_runtime,
        )
        hit["source"] = "memory_bank"
        if client_err:
            hit["client_error"] = client_err
        return hit

    def get_playback_status(self) -> dict[str, Any]:
        live: dict[str, Any] = {}
        if self.capabilities().get("verifyPlayback"):
            live = self._verify_playing(None)
        return {
            "ok": True,
            "last": self._last,
            "live": live,
            "capabilities": self.capabilities(),
            "scopes": self._scope_store.snapshot() if self._scope_store else None,
        }
