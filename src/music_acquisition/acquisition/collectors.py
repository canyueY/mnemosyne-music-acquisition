# -*- coding: utf-8 -*-
"""Collector registry — maps collector_id → source method name."""

from __future__ import annotations

COLLECTOR_REGISTRY = {
    "profile": "get_profile",
    "liked_songs": "get_liked_songs",
    "playlists": "get_playlists",
    "playlist_tracks": "get_playlist_tracks",
    "daily_songs": "get_daily_songs",
    "daily_playlists": "get_daily_playlists",
    "playback_history": "get_playback_history",
    "favorite_artists": "get_favorite_artists",
}
