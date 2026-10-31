# -*- coding: utf-8 -*-
"""Netease Source Adapter — QR login + config-bind + web collectors."""

from __future__ import annotations

import asyncio
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
        playlist_limit: int | None = None,
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
        self._page_cache = None
        self._health_checked_at = 0.0

    def set_checkpoint_store(self, store):
        self._page_cache = store

    async def begin_config_bind(self):
        sid = f"sess_{secrets.token_hex(8)}"
        self._session = sid
        self._sessions[sid] = {"mode": "config_bind"}
        return AuthorizationChallenge(session_id=sid, expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())

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
            verified_at="",
            verification_status="implemented_unverified",
        )

    async def begin_authorization(self) -> AuthorizationChallenge:
        sid = f"sess_{secrets.token_hex(8)}"
        self._session = sid
        self._qr = await asyncio.to_thread(qr_auth.create_qr_session)
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
        self._authorized = bool(self._cookie)
        return AuthorizationStatus(
            session_id=session_id,
            status="authorized" if self._cookie else "public_only",
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
        self._qr = await asyncio.to_thread(qr_auth.poll_qr, self._qr)
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
            session_id=session_id, status="failed", detail="AUTH_FAILED"
        )

    async def check_authorization(self) -> AuthorizationStatus:
        if self._cookie:
            import asyncio, time
            if time.monotonic() - self._health_checked_at < 30:
                return AuthorizationStatus(status="valid", subject_id=self.subject_id)
            try:
                profile = await asyncio.to_thread(qr_auth.fetch_account, self._cookie)
            except Exception:
                return AuthorizationStatus(status="unknown", detail="AUTH_HEALTH_UNAVAILABLE")
            uid = str(profile.get("userId") or "")
            if profile.get("code") != 200 or not uid.isdigit():
                self._cookie, self._authorized = "", False
                return AuthorizationStatus(status="expired", detail="LOGIN_REQUIRED")
            if self.subject_id not in {"netease_local", uid}:
                return AuthorizationStatus(status="failed", detail="ACCOUNT_MISMATCH")
            self.subject_id = uid
            self._health_checked_at = time.monotonic()
            return AuthorizationStatus(status="valid", subject_id=self.subject_id)
        if self.favorite_playlist_id:
            return AuthorizationStatus(
                status="public_only", subject_id=self.subject_id, detail="config_bind_is_not_account_authorization"
            )
        return AuthorizationStatus(status="unbound")

    async def get_profile(self) -> CollectionResult:
        self._require()
        if self._cookie:
            prof = await asyncio.to_thread(qr_auth.fetch_account, self._cookie)
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
            pid = await asyncio.to_thread(self._guess_liked_playlist_id) or ""
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
        tracks, expected, complete = await asyncio.to_thread(api.fetch_playlist_tracks,
            int(pid), limit=self.playlist_limit, cookie=self._cookie, page_cache=self._page_cache
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
            items, seen, complete, offset = [], set(), True, 0
            for _ in range(1000):
                body, _ = await asyncio.to_thread(qr_auth._request, "https://music.163.com/api/user/playlist",
                    params={"uid": str(self.subject_id), "limit": "100", "offset": str(offset)}, cookie=self._cookie)
                if int(body.get("code") or 0) in {301, 401, 403}:
                    raise PermissionError("LOGIN_REQUIRED")
                if int(body.get("code") or 0) != 200:
                    complete = False
                    break
                rows = body.get("playlist")
                if not isinstance(rows, list):
                    raise ValueError("SOURCE_FORMAT_ERROR")
                added = 0
                for pl in rows:
                    if not isinstance(pl, dict) or not pl.get("id") or str(pl["id"]) in seen:
                        continue
                    seen.add(str(pl["id"]))
                    added += 1
                    items.append({"playlist_id": f"netease:{pl['id']}", "provider": "netease",
                        "provider_playlist_id": str(pl["id"]), "name": str(pl.get("name") or ""),
                        "track_count": pl.get("trackCount"), "owner_id": str((pl.get("creator") or {}).get("userId") or ""),
                        "special_type": pl.get("specialType"), "extensions": pl})
                if not body.get("more"):
                    break
                if not added:
                    complete = False
                    break
                offset += len(rows)
            else:
                complete = False
            return CollectionResult(collection_id=f"col_{secrets.token_hex(4)}", collector_id="playlists",
                subject_id=self.subject_id, collected_at=_now(), status="success" if complete else "partial",
                complete=complete, records=items, actual_count=len(items), expected_count=len(items) if complete else None)
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
                complete=False,
                status="partial", warnings=["user_playlist_catalog_incomplete_without_cookie"],
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
        tracks, expected, complete = await asyncio.to_thread(api.fetch_playlist_tracks,
            int(pid), limit=self.playlist_limit, cookie=self._cookie, page_cache=self._page_cache
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
            raw, _ = await asyncio.to_thread(qr_auth._request, qr_auth.DAILY, cookie=self._cookie)
            code = int(raw.get("code") or 0)
            if code in {301, 401, 403}:
                raise PermissionError("LOGIN_REQUIRED")
            if code != 200:
                raise RuntimeError(f"HTTP_{code}")
            songs = (raw.get("data") or {}).get("dailySongs")
            if not isinstance(songs, list):
                raise ValueError("DAILY_SOURCE_FORMAT_ERROR")
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
                expected_count=len(songs),
                complete=True,
                warnings=["personalized_with_cookie"],
                source_metadata={"code": raw.get("code")},
            )
        tracks = await asyncio.to_thread(api.fetch_daily_songs, limit=30)
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
            prof = await asyncio.to_thread(qr_auth.fetch_account, self._cookie)
            uid = str(prof.get("userId") or uid)
            if str(uid).isdigit():
                self.subject_id = str(uid)
        week = await asyncio.to_thread(weapi.fetch_play_record, uid, cookie=self._cookie, kind=1)
        records = list(week.get("records") or [])
        warnings = ["source=weapi_v1_play_record"]
        if int(week.get("code") or 0) != 200 and not records:
            # fallback all-time
            all_time = await asyncio.to_thread(weapi.fetch_play_record, uid, cookie=self._cookie, kind=0)
            records = list(all_time.get("records") or [])
            warnings.append(f"week_code={week.get('code')};tried_all={all_time.get('code')}")
            week = all_time
        ok = int(week.get("code") or 0) == 200
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

        page = await asyncio.to_thread(weapi.fetch_sub_artists, cookie=self._cookie, limit=100, offset=0)
        records = list(page.get("records") or [])
        # paginate lightly
        offset = 100
        while page.get("has_more") and offset < 100000:
            page = await asyncio.to_thread(weapi.fetch_sub_artists, cookie=self._cookie, limit=100, offset=offset)
            records.extend(page.get("records") or [])
            offset += 100
            if not page.get("has_more"):
                break
        code = int(page.get("code") or 0)
        ok = code == 200 and not page.get("has_more")
        if code in {301, 401, 403}:
            raise PermissionError("LOGIN_REQUIRED")
        records = list({str(r.get("provider_artist_id") or r.get("artist_id")): r for r in records}.values())
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
        self._health_checked_at = 0.0
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
        # A name or first catalog entry is not proof of the account's liked list.
        for offset in range(0, 100000, 100):
            body, _ = qr_auth._request("https://music.163.com/api/user/playlist",
                params={"uid": str(self.subject_id), "limit": "100", "offset": str(offset)}, cookie=self._cookie)
            if int(body.get("code") or 0) != 200:
                return None
            rows = body.get("playlist") or []
            for pl in rows:
                if isinstance(pl, dict) and pl.get("specialType") == 5 and str((pl.get("creator") or {}).get("userId")) == str(self.subject_id):
                    return str(pl["id"])
            if not rows or not body.get("more"):
                break
        return None

    def _require(self) -> None:
        if not self._authorized and not self._cookie:
            raise PermissionError("LOGIN_REQUIRED")
