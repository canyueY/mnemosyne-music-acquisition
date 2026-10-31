# -*- coding: utf-8 -*-
"""MusicSourceAdapter — replaceable Netease (or other) source boundary."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from music_acquisition.domain.models import (
    AuthorizationChallenge,
    AuthorizationStatus,
    CollectionResult,
    SourceCapabilities,
)


@runtime_checkable
class MusicSourceAdapter(Protocol):
    async def get_capabilities(self) -> SourceCapabilities: ...

    async def begin_authorization(self) -> AuthorizationChallenge: ...

    async def poll_authorization(self, session_id: str) -> AuthorizationStatus: ...

    async def check_authorization(self) -> AuthorizationStatus: ...

    async def get_profile(self) -> CollectionResult: ...

    async def get_liked_songs(self) -> CollectionResult: ...

    async def get_playlists(self) -> CollectionResult: ...

    async def get_playlist_tracks(
        self, playlist_id: str, cursor: str | None = None
    ) -> CollectionResult: ...

    async def get_daily_songs(self) -> CollectionResult: ...

    async def get_daily_playlists(self) -> CollectionResult: ...

    async def get_playback_history(self) -> CollectionResult: ...

    async def revoke_authorization(self) -> None: ...
