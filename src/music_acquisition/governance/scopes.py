# -*- coding: utf-8 -*-
"""Scope + consent gate — all optional scopes default OFF."""

from __future__ import annotations

from music_acquisition.domain.models import MusicConsent, MusicScopeConfig

ALL_SCOPES = [
    "music.profile.basic",
    "music.library.liked_songs",
    "music.library.playlists",
    "music.library.playlist_tracks",
    "music.library.favorite_artists",
    "music.recommend.daily_songs",
    "music.recommend.daily_playlists",
    "music.history.playback",
    "music.playback.events",
    "music.playback.control",
]

DEFAULT_SCOPES_OFF: list[str] = []  # explicit: empty until user enables

COLLECTOR_SCOPES = {
    "liked_songs": ["music.library.liked_songs"],
    "playlists": ["music.library.playlists"],
    "playlist_tracks": ["music.library.playlist_tracks"],
    "daily_songs": ["music.recommend.daily_songs"],
    "daily_playlists": ["music.recommend.daily_playlists"],
    "playback_history": ["music.history.playback"],
    "favorite_artists": ["music.library.favorite_artists"],
    "profile": ["music.profile.basic"],
}


class PermissionGate:
    def __init__(self) -> None:
        self._scopes: dict[str, MusicScopeConfig] = {}
        self._consents: dict[str, MusicConsent] = {}

    def configure(self, config: MusicScopeConfig) -> None:
        unknown = [s for s in config.scopes if s not in ALL_SCOPES]
        if unknown:
            raise ValueError(f"unknown_scopes:{unknown}")
        self._scopes[config.owner_user_id] = config

    def get_scopes(self, owner_user_id: str) -> list[str]:
        cfg = self._scopes.get(owner_user_id)
        return list(cfg.scopes) if cfg else list(DEFAULT_SCOPES_OFF)

    def allow_collector(self, owner_user_id: str, collector_id: str) -> bool:
        needed = COLLECTOR_SCOPES.get(collector_id, [])
        if not needed:
            return False
        have = set(self.get_scopes(owner_user_id))
        return all(s in have for s in needed)

    def set_consent(self, consent: MusicConsent) -> None:
        self._consents[consent.owner_user_id] = consent

    def consent_active(self, owner_user_id: str) -> bool:
        c = self._consents.get(owner_user_id)
        return bool(c and c.status == "active")
