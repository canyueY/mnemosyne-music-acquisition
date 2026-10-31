# -*- coding: utf-8 -*-
"""Netease Source Adapter — QR login + config-bind + web collectors."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from music_acquisition.domain.models import (
    AuthorizationChallenge,
    AuthorizationStatus,
    CollectionResult,
    MusicProfile,
    SourceCapabilities,
)
from music_acquisition.source.netease import http_client as api
from music_acquisition.source.netease import qr_auth


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class NeteaseSourceAdapter:
    """Live-verified 2026-10-31:

    - qrLogin: OK (unikey → 801/802/803)
    - dailySongs with cookie: personalized (0 overlap vs anonymous pool)
    - playbackHistory via weapi ``/weapi/v1/play/record`` (plain /api 404)
    - user playlist list with cookie: OK
    """

    def __init__(
        self,
        *,
        favorite_playlist_id: str | int | None = None,
        subject_id: str = "netease_local",
        playlist_limit: int = 500,
        cookie: str = "",
    ) -> None:
        self.favorite_playlist_id = (
            str(favorite_playlist_id).strip() if favorite_playlist_id is not None else ""
        )
        self.subject_id = subject_id
        self.playlist_limit = playlist_limit
        self._cookie = cookie.strip()
        self._authorized = bool(self._cookie or self.favorite_playlist_id)
        self._session: str | None = None
        self._qr: qr_auth.QrSession | None = None
        self._sessions: dict[str, dict[str, Any]] = {}

    async def get_capabilities(self) -> SourceCapabilities:
        authed = bool(self._cookie)
        return SourceCapabilities(
            provider="netease",
            features={
                "qrLogin": True,
                "configBind": True,
                "credentialRefresh": False,
                "likedSongs": bool(self.favorite_playlist_id) or authed,
                "playlists": authed or bool(self.favorite_playlist_id),
                "playlistTracks": True,
                "dailySongs": True,
                "dailySongsPersonalized": authed,
                "dailyPlaylists": False,
                "playbackHistory": authed,
                "playbackHistoryWeapi": authed,
                "favoriteArtists": authed,
                "playbackControl": False,
                "realtimeEvents": False,
            },
            limitations=[
                "playback_history_requires_weapi_and_cookie",
                "favorite_artists_requires_weapi_and_cookie",
                "daily_without_cookie_is_public_pool",
                "no_ncm_cli_hard_dependency",
            ],
            verified_at=_now(),
        )

    async def begin_authorization(self) -> AuthorizationChallenge:
        sid = f"sess_{secrets.token_hex(8)}"
        self._session = sid
        self._qr = qr_auth.create_qr_session()
        exp = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self._sessions[sid] = {"unikey": self._qr.unikey, "mode": "qr"}
        return AuthorizationChallenge(
            session_id=sid,
            provider="netease",
            qr_key=self._qr.unikey,
            qr_url=self._qr.scan_url,
            expires_at=exp,
            status="waiting_scan",
        )

    def complete_config_bind(
        self,
        session_id: str,
        *,
        favorite_playlist_id: str | int,
        subject_id: str | None = None,
    ) -> AuthorizationStatus:
        if self._session and session_id != self._session and session_id not in self._sessions:
            # allow bind without prior begin for CLI
            self._session = session_id
        pid = str(favorite_playlist_id).strip()
        if not pid.isdigit():
            return AuthorizationStatus(
                session_id=session_id, status="failed", detail="favorite_playlist_id_must_be_digits"
            )
        self.favorite_playlist_id = pid
        if subject_id:
            self.subject_id = str(subject_id)
        self._authorized = True
        return AuthorizationStatus(
            session_id=session_id,
            status="authorized",
            subject_id=self.subject_id,
            detail="config_bind",
        )

    async def poll_authorization(self, session_id: str) -> AuthorizationStatus:
        if self._qr is None or (self._session and session_id != self._session):
            # config-bind pending
            if self._authorized and self._cookie:
                return AuthorizationStatus(
                    session_id=session_id, status="authorized", subject_id=self.subject_id
                )
            return AuthorizationStatus(
                session_id=session_id, status="pending", detail="awaiting_qr_or_config_bind"
            )
        self._qr = qr_auth.poll_qr(self._qr)
        if self._qr.status == "waiting_scan":
            return AuthorizationStatus(session_id=session_id, status="pending", detail="waiting_scan")
        if self._qr.status == "confirming":
            return AuthorizationStatus(
                session_id=session_id, status="confirming", detail="waiting_confirm"
            )
        if self._qr.status == "expired":
            return AuthorizationStatus(session_id=session_id, status="expired")
        if self._qr.status == "authorized":
            self._cookie = self._qr.cookie
            self._authorized = True
            uid = (self._qr.profile or {}).get("userId")
            if uid:
                self.subject_id = str(uid)
            return AuthorizationStatus(
                session_id=session_id,
                status="authorized",
                subject_id=self.subject_id,
                detail="qr_login",
            )
        return AuthorizationStatus(
            session_id=session_id, status="failed", detail=str(self._qr.raw_last)
        )

    async def check_authorization(self) -> AuthorizationStatus:
        if self._cookie:
            return AuthorizationStatus(status="valid", subject_id=self.subject_id)
        if self._authorized and self.favorite_playlist_id:
            return AuthorizationStatus(
                status="valid", subject_id=self.subject_id, detail="config_bind_only"
            )
        return AuthorizationStatus(status="unbound")

    async def get_profile(self) -> CollectionResult:
        self._require()
        if self._cookie:
            prof = qr_auth.fetch_account(self._cookie)
            p = MusicProfile(
                subject_id=str(prof.get("userId") or self.subject_id),
                display_name=str(prof.get("nickname") or ""),
                avatar_url=str(prof.get("avatarUrl") or ""),
                raw={"code": prof.get("code")},
            )
        else:
            p = MusicProfile(subject_id=self.subject_id, display_name=self.subject_id)
        return CollectionResult(
            collection_id=f"col_{secrets.token_hex(4)}",
            collector_id="profile",
            subject_id=p.subject_id,
            collected_at=_now(),
            records=[p.model_dump()],
            actual_count=1,
            complete=True,
        )

    async def get_liked_songs(self) -> CollectionResult:
        self._require()
        # prefer configured favorite playlist; else try discover from user playlists
        pid = self.favorite_playlist_id
        if not pid and self._cookie:
            pid = self._guess_liked_playlist_id() or ""
            if pid:
                self.favorite_playlist_id = pid
        if not pid:
            return CollectionResult(
                collection_id=f"col_{secrets.token_hex(4)}",
                collector_id="liked_songs",
                subject_id=self.subject_id,
                collected_at=_now(),
                status="unsupported",
                complete=False,
                records=[],
                actual_count=0,
                warnings=["favorite_playlist_id_missing"],
            )
        tracks, expected, complete = api.fetch_playlist_tracks(
            int(pid), limit=self.playlist_limit
        )
        return CollectionResult(
            collection_id=f"col_{secrets.token_hex(4)}",
            collector_id="liked_songs",
            subject_id=self.subject_id,
            collected_at=_now(),
            status="success" if complete else "partial",
            complete=complete,
            records=tracks,
            expected_count=expected,
            actual_count=len(tracks),
            source_metadata={"playlist_id": pid},
        )

    async def get_playlists(self) -> CollectionResult:
        self._require()
        if self._cookie:
            body, _ = qr_auth._request(
                "https://music.163.com/api/user/playlist",
                params={"uid": str(self.subject_id), "limit": "100"},
                cookie=self._cookie,
            )
            items = []
            for pl in body.get("playlist") or []:
                if not isinstance(pl, dict):
                    continue
                items.append(
                    {
                        "playlist_id": f"netease:{pl.get('id')}",
                        "provider": "netease",
                        "provider_playlist_id": str(pl.get("id")),
                        "name": str(pl.get("name") or ""),
                        "track_count": pl.get("trackCount"),
                        "owner_id": str(self.subject_id),
                    }
                )
            return CollectionResult(
                collection_id=f"col_{secrets.token_hex(4)}",
                collector_id="playlists",
                subject_id=self.subject_id,
                collected_at=_now(),
                status="success" if int(body.get("code") or 0) == 200 else "partial",
                complete=True,
                records=items,
                actual_count=len(items),
            )
        if self.favorite_playlist_id:
            rec = {
                "playlist_id": f"netease:{self.favorite_playlist_id}",
                "provider": "netease",
                "provider_playlist_id": self.favorite_playlist_id,
                "name": "我喜欢的音乐",
                "owner_id": self.subject_id,
            }
            return CollectionResult(
                collection_id=f"col_{secrets.token_hex(4)}",
                collector_id="playlists",
                subject_id=self.subject_id,
                collected_at=_now(),
                records=[rec],
                actual_count=1,
                complete=True,
                warnings=["user_playlist_catalog_incomplete_without_cookie"],
            )
        return CollectionResult(
            collection_id=f"col_{secrets.token_hex(4)}",
            collector_id="playlists",
            subject_id=self.subject_id,
            collected_at=_now(),
            status="failed",
            complete=False,
            records=[],
            actual_count=0,
        )

    async def get_playlist_tracks(
        self, playlist_id: str, cursor: str | None = None
    ) -> CollectionResult:
        pid = str(playlist_id).replace("netease:", "")
        tracks, expected, complete = api.fetch_playlist_tracks(
            int(pid), limit=self.playlist_limit
        )
        return CollectionResult(
            collection_id=f"col_{secrets.token_hex(4)}",
            collector_id="playlist_tracks",
            subject_id=self.subject_id,
            collected_at=_now(),
            status="success" if complete else "partial",
            complete=complete,
            records=tracks,
            expected_count=expected,
            actual_count=len(tracks),
            cursor=cursor,
            source_metadata={"playlist_id": pid},
        )

    async def get_daily_songs(self) -> CollectionResult:
        if self._cookie:
            raw, _ = qr_auth._request(qr_auth.DAILY, cookie=self._cookie)
            songs = ((raw.get("data") or {}).get("dailySongs") or [])
            tracks = []
            for s in songs:
                if isinstance(s, dict):
                    t = api.parse_track(s)
                    if t:
                        tracks.append(t)
            return CollectionResult(
                collection_id=f"col_{secrets.token_hex(4)}",
                collector_id="daily_songs",
                subject_id=self.subject_id,
                collected_at=_now(),
                records=tracks,
                actual_count=len(tracks),
                complete=True,
                warnings=["personalized_with_cookie"],
                source_metadata={"code": raw.get("code")},
            )
        tracks = api.fetch_daily_songs(limit=30)
        return CollectionResult(
            collection_id=f"col_{secrets.token_hex(4)}",
            collector_id="daily_songs",
            subject_id=self.subject_id,
            collected_at=_now(),
            records=tracks,
            actual_count=len(tracks),
            complete=True,
            warnings=["public_daily_pool_not_account_personalized"],
        )

    async def get_daily_playlists(self) -> CollectionResult:
        return CollectionResult(
            collection_id=f"col_{secrets.token_hex(4)}",
            collector_id="daily_playlists",
            subject_id=self.subject_id,
            collected_at=_now(),
            status="unsupported",
            complete=False,
            records=[],
            actual_count=0,
            warnings=["daily_playlists_unsupported"],
        )

    async def get_playback_history(self) -> CollectionResult:
        if not self._cookie:
            return CollectionResult(
                collection_id=f"col_{secrets.token_hex(4)}",
                collector_id="playback_history",
                subject_id=self.subject_id,
                collected_at=_now(),
                status="unsupported",
                complete=False,
                records=[],
                actual_count=0,
                warnings=["playback_history_requires_qr_or_cookie"],
            )
        from music_acquisition.source.netease import weapi

        uid = self.subject_id
        if not str(uid).isdigit():
            prof = qr_auth.fetch_account(self._cookie)
            uid = str(prof.get("userId") or uid)
            if str(uid).isdigit():
                self.subject_id = str(uid)
        week = weapi.fetch_play_record(uid, cookie=self._cookie, kind=1)
        records = list(week.get("records") or [])
        warnings = ["source=weapi_v1_play_record"]
        if int(week.get("code") or 0) != 200 and not records:
            # fallback all-time
            all_time = weapi.fetch_play_record(uid, cookie=self._cookie, kind=0)
            records = list(all_time.get("records") or [])
            warnings.append(f"week_code={week.get('code')};tried_all={all_time.get('code')}")
            week = all_time
        ok = int(week.get("code") or 0) == 200 and bool(records)
        return CollectionResult(
            collection_id=f"col_{secrets.token_hex(4)}",
            collector_id="playback_history",
            subject_id=self.subject_id,
            collected_at=_now(),
            status="success" if ok else ("partial" if records else "failed"),
            complete=ok,
            records=records,
            actual_count=len(records),
            warnings=warnings,
            source_metadata={"code": week.get("code"), "msg": week.get("msg")},
        )

    async def get_favorite_artists(self) -> CollectionResult:
        if not self._cookie:
            return CollectionResult(
                collection_id=f"col_{secrets.token_hex(4)}",
                collector_id="favorite_artists",
                subject_id=self.subject_id,
                collected_at=_now(),
                status="unsupported",
                complete=False,
                records=[],
                actual_count=0,
                warnings=["favorite_artists_requires_qr_or_cookie"],
            )
        from music_acquisition.source.netease import weapi

        page = weapi.fetch_sub_artists(cookie=self._cookie, limit=100, offset=0)
        records = list(page.get("records") or [])
        # paginate lightly
        offset = 100
        while page.get("has_more") and offset < 500:
            page = weapi.fetch_sub_artists(cookie=self._cookie, limit=100, offset=offset)
            records.extend(page.get("records") or [])
            offset += 100
            if not page.get("has_more"):
                break
        code = int(page.get("code") or 0)
        ok = code == 200
        return CollectionResult(
            collection_id=f"col_{secrets.token_hex(4)}",
            collector_id="favorite_artists",
            subject_id=self.subject_id,
            collected_at=_now(),
            status="success" if ok else ("partial" if records else "failed"),
            complete=ok,
            records=records,
            actual_count=len(records),
            expected_count=int(page.get("count") or len(records)),
            warnings=["source=weapi_artist_sublist"],
            source_metadata={"code": code, "msg": page.get("msg")},
        )

    def import_cookie(self, cookie: str, *, subject_id: str | None = None) -> None:
        """Restore login cookie from Memory Bank credential store."""
        self._cookie = (cookie or "").strip()
        if self._cookie:
            self._authorized = True
        if subject_id:
            self.subject_id = str(subject_id)

    def export_cookie(self) -> str:
        """Return cookie for secure persistence (caller must not log)."""
        return self._cookie

    async def revoke_authorization(self) -> None:
        self._authorized = False
        self._cookie = ""
        self._qr = None
        self._session = None

    def export_cookie_masked(self) -> str:
        return "MUSIC_U=<present>" if "MUSIC_U=" in self._cookie else ""

    def _guess_liked_playlist_id(self) -> str | None:
        try:
            body, _ = qr_auth._request(
                "https://music.163.com/api/user/playlist",
                params={"uid": str(self.subject_id), "limit": "50"},
                cookie=self._cookie,
            )
            for pl in body.get("playlist") or []:
                if not isinstance(pl, dict):
                    continue
                name = str(pl.get("name") or "")
                # 常见「我喜欢的音乐」
                if "喜欢" in name or pl.get("specialType") == 5:
                    return str(pl.get("id"))
            # 第一份常为红心
            pls = body.get("playlist") or []
            if pls and isinstance(pls[0], dict) and pls[0].get("id"):
                return str(pls[0]["id"])
        except Exception:
            return None
        return None

    def _require(self) -> None:
        if not self._authorized and not self._cookie:
            raise PermissionError("LOGIN_REQUIRED")
