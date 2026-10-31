# -*- coding: utf-8 -*-
"""Mock Netease adapter — contract tests without network / DSH / password."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from music_acquisition.domain.models import (
    AuthorizationChallenge,
    AuthorizationStatus,
    CollectionResult,
    MusicArtistRef,
    MusicPlaylist,
    MusicProfile,
    MusicTrack,
    SourceCapabilities,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _track(i: int) -> MusicTrack:
    return MusicTrack(
        track_id=f"mock_track_{i}",
        provider_track_id=str(1000 + i),
        title=f"Mock Song {i}",
        artists=[MusicArtistRef(provider_artist_id=str(10 + i % 3), name=f"Artist {i % 3}")],
        metadata={"source": "mock", "fetched_at": _now(), "schema_version": "music.canonical.v1"},
    )


class MockNeteaseAdapter:
    def __init__(self, *, auto_authorize: bool = True, liked_count: int = 5) -> None:
        self.auto_authorize = auto_authorize
        self.liked_count = liked_count
        self._authorized = False
        self._subject_id = "mock_uid_1"
        self._session: str | None = None
        self._polls = 0

    async def get_capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            provider="netease",
            features={
                "qrLogin": True,
                "credentialRefresh": False,
                "likedSongs": True,
                "playlists": True,
                "playlistTracks": True,
                "dailySongs": True,
                "dailyPlaylists": True,
                "playbackHistory": True,
                "favoriteArtists": False,
                "playbackControl": False,
                "realtimeEvents": False,
            },
            limitations=["mock_adapter", "no_real_network"],
            verified_at=_now(),
            verification_status="verified_synthetic",
        )

    async def begin_authorization(self) -> AuthorizationChallenge:
        sid = f"sess_{secrets.token_hex(8)}"
        self._session = sid
        self._polls = 0
        self._authorized = False
        exp = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return AuthorizationChallenge(
            session_id=sid,
            qr_key=f"mock-key-{sid}",
            qr_url=f"mock://qr/{sid}",
            expires_at=exp,
            status="waiting_scan",
        )

    async def poll_authorization(self, session_id: str) -> AuthorizationStatus:
        if session_id != self._session:
            return AuthorizationStatus(session_id=session_id, status="failed", detail="unknown_session")
        self._polls += 1
        if self.auto_authorize and self._polls >= 1:
            self._authorized = True
            return AuthorizationStatus(
                session_id=session_id,
                status="authorized",
                subject_id=self._subject_id,
            )
        return AuthorizationStatus(session_id=session_id, status="pending")

    async def check_authorization(self) -> AuthorizationStatus:
        if self._authorized:
            return AuthorizationStatus(status="valid", subject_id=self._subject_id)
        return AuthorizationStatus(status="unbound")

    async def get_profile(self) -> CollectionResult:
        self._require_auth()
        p = MusicProfile(subject_id=self._subject_id, display_name="Mock User")
        return CollectionResult(
            collection_id=f"col_{secrets.token_hex(4)}",
            collector_id="profile",
            subject_id=self._subject_id,
            collected_at=_now(),
            records=[p.model_dump()],
            actual_count=1,
            complete=True,
        )

    async def get_liked_songs(self) -> CollectionResult:
        self._require_auth()
        tracks = [_track(i) for i in range(self.liked_count)]
        return CollectionResult(
            collection_id=f"col_{secrets.token_hex(4)}",
            collector_id="liked_songs",
            subject_id=self._subject_id,
            collected_at=_now(),
            records=[t.model_dump() for t in tracks],
            expected_count=self.liked_count,
            actual_count=len(tracks),
            complete=True,
        )

    async def get_playlists(self) -> CollectionResult:
        self._require_auth()
        pl = MusicPlaylist(
            playlist_id="pl_1",
            provider_playlist_id="9001",
            name="Mock Playlist",
            track_count=2,
        )
        return CollectionResult(
            collection_id=f"col_{secrets.token_hex(4)}",
            collector_id="playlists",
            subject_id=self._subject_id,
            collected_at=_now(),
            records=[pl.model_dump()],
            actual_count=1,
            complete=True,
        )

    async def get_playlist_tracks(
        self, playlist_id: str, cursor: str | None = None
    ) -> CollectionResult:
        self._require_auth()
        tracks = [_track(i) for i in range(2)]
        return CollectionResult(
            collection_id=f"col_{secrets.token_hex(4)}",
            collector_id="playlist_tracks",
            subject_id=self._subject_id,
            collected_at=_now(),
            records=[t.model_dump() for t in tracks],
            expected_count=2,
            actual_count=2,
            complete=True,
            source_metadata={"playlist_id": playlist_id, "cursor": cursor},
        )

    async def get_daily_songs(self) -> CollectionResult:
        self._require_auth()
        tracks = [_track(i) for i in range(3)]
        return CollectionResult(
            collection_id=f"col_{secrets.token_hex(4)}",
            collector_id="daily_songs",
            subject_id=self._subject_id,
            collected_at=_now(),
            records=[t.model_dump() for t in tracks],
            actual_count=3,
            complete=True,
        )

    async def get_daily_playlists(self) -> CollectionResult:
        return await self.get_playlists()

    async def get_playback_history(self) -> CollectionResult:
        self._require_auth()
        return CollectionResult(
            collection_id=f"col_{secrets.token_hex(4)}",
            collector_id="playback_history",
            subject_id=self._subject_id,
            collected_at=_now(),
            records=[{"provider_track_id": "1000", "played_at": _now()}],
            actual_count=1,
            complete=True,
        )

    async def get_favorite_artists(self) -> CollectionResult:
        self._require_auth()
        return CollectionResult(
            collection_id=f"col_{secrets.token_hex(4)}",
            collector_id="favorite_artists",
            subject_id=self._subject_id,
            collected_at=_now(),
            records=[
                {"provider_artist_id": "1", "name": "Mock Artist"},
                {"provider_artist_id": "2", "name": "Another Artist"},
            ],
            actual_count=2,
            complete=True,
        )

    async def revoke_authorization(self) -> None:
        self._authorized = False
        self._session = None

    def _require_auth(self) -> None:
        if not self._authorized:
            raise PermissionError("LOGIN_REQUIRED")
