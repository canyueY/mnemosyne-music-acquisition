# -*- coding: utf-8 -*-
"""Optional playback — prefers Murasame bridge when available; else unsupported.

切歌 / 序号点播优先走源歌单边界（Memory Bank playlist_scopes），
避免把 runtime 播放队列下标当成「歌单第 N 首」。
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from music_acquisition.playlist_scope_store import PlaylistScopeStore


class MusicPlaybackService:
    def __init__(
        self,
        *,
        enable_murasaame_bridge: bool = True,
        scope_store: "PlaylistScopeStore | None" = None,
    ) -> None:
        self._bridge = enable_murasaame_bridge
        self._scope_store = scope_store
        self._last: dict[str, Any] = {}

    def capabilities(self) -> dict[str, bool]:
        ok = False
        if self._bridge:
            try:
                from Murasame import netease_music as NM  # type: ignore

                ok = bool(getattr(NM, "is_enabled", lambda: True)())
            except Exception:
                ok = False
        return {
            "playbackControl": ok,
            "playSong": ok,
            "playPlaylist": ok,
            "playDaily": ok,
            "playNextInScope": ok,
            "roundtripLookup": self._scope_store is not None or ok,
            "verifyPlayback": False,
        }

    def play_song(self, track_id: str) -> dict[str, Any]:
        if not self.capabilities().get("playSong"):
            return {"ok": False, "error": "PLAYBACK_UNSUPPORTED"}
        try:
            from Murasame import netease_music as NM  # type: ignore

            sid = int(str(track_id).replace("netease:", ""))
            result = NM.launch_cloudmusic_song(sid)
            self._last = {"target": sid, "result": result}
            return {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "PLAY_FAILED", "detail": str(exc)}

    def play_playlist(
        self, playlist_id: str, *, track_id: str | None = None, index: int | None = None
    ) -> dict[str, Any]:
        if track_id is not None and index is not None:
            return {"ok": False, "error": "AMBIGUOUS_TARGET"}
        if not self.capabilities().get("playPlaylist"):
            return {"ok": False, "error": "PLAYBACK_UNSUPPORTED"}
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
            self._last = {"playlist": pid, "result": result}
            return {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "PLAY_FAILED", "detail": str(exc)}

    def play_daily(
        self, date: str, *, track_id: str | None = None, index: int | None = None
    ) -> dict[str, Any]:
        if track_id is not None and index is not None:
            return {"ok": False, "error": "AMBIGUOUS_TARGET"}
        if not self.capabilities().get("playDaily"):
            return {"ok": False, "error": "PLAYBACK_UNSUPPORTED"}
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
            return {"ok": True, "result": result, "note": "opens_daily_page"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "PLAY_FAILED", "detail": str(exc)}

    def play_next_in_scope(self, *, delta: int = 1) -> dict[str, Any]:
        """下一首/上一首：锚定源歌单（桌宠 CDP + Bank 边界）。"""
        if not self.capabilities().get("playNextInScope"):
            return {"ok": False, "error": "PLAYBACK_UNSUPPORTED"}
        try:
            from Murasame import netease_music as NM  # type: ignore
            from Murasame.utils import get_config  # type: ignore

            action = "next" if int(delta) >= 0 else "prev"
            result = NM.media_control(action, cfg=get_config())
            # 刷一份边界进 Bank，便于跨进程往返
            if self._scope_store is not None:
                try:
                    self._scope_store.ingest_from_murasame_registry()
                except Exception:
                    pass
            self._last = {"action": action, "result": result}
            return {"ok": bool(result.get("ok")), "result": result}
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
        return {
            "ok": True,
            "last": self._last,
            "capabilities": self.capabilities(),
            "scopes": self._scope_store.snapshot() if self._scope_store else None,
        }
