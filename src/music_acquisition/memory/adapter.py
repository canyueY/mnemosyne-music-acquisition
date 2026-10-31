# -*- coding: utf-8 -*-
"""Music Memory Adapter — maps snapshots → Evidence requests (no direct DB).

Mnemosyne is the only long-term music cognition authority.
This adapter only builds payloads / outbox entries for Service Contract submit.
"""

from __future__ import annotations

import secrets
from typing import Any

from music_acquisition.contracts.host_runtime import HostRuntime
from music_acquisition.domain.models import MusicSnapshot

_ACTION_BY_TYPE = {
    "liked_songs": ("user_action", "saved", "track"),
    "playback_history": ("user_action", "played", "track"),
    "daily_songs": ("catalog", "recommendation_seen", "track"),
    "playlists": ("catalog", "playlist_membership", "playlist"),
    "playlist_tracks": ("catalog", "contains", "track"),
    "favorite_artists": ("user_action", "follows", "artist"),
}


class MusicMemoryAdapter:
    """Never opens Mnemosyne sqlite; only emits evidence candidates + outbox."""

    def __init__(self, host: HostRuntime) -> None:
        self._host = host

    def map_snapshot_to_evidence(
        self,
        snapshot: MusicSnapshot,
        *,
        allow_create_memory: bool = False,
    ) -> list[dict[str, Any]]:
        """Recommendation exposure must NOT become long-term like."""
        evidence: list[dict[str, Any]] = []
        etype = {
            "liked_songs": "music.favorite_added",
            "playback_history": "music.played",
            "daily_songs": "music.recommendation_seen",
            "playlists": "music.playlist_membership",
            "playlist_tracks": "music.playlist_contains",
            "favorite_artists": "music.artist_followed",
        }.get(snapshot.data_type, "music.observed")
        data_kind, action, default_target = _ACTION_BY_TYPE.get(
            snapshot.data_type, ("catalog", "observed", "track")
        )
        imported_at = self._host.clock.now_iso()

        for rec in snapshot.records:
            tid = ""
            title = ""
            artists: Any = ""
            playlist_id = ""
            if isinstance(rec, dict):
                tid = str(
                    rec.get("provider_track_id")
                    or rec.get("track_id")
                    or rec.get("provider_artist_id")
                    or rec.get("artist_id")
                    or rec.get("provider_playlist_id")
                    or rec.get("playlist_id")
                    or ""
                )
                title = str(rec.get("title") or rec.get("name") or "")
                artists = rec.get("artists") or rec.get("artist") or rec.get("ar") or ""
                playlist_id = str(
                    rec.get("provider_playlist_id")
                    or rec.get("playlist_id")
                    or rec.get("parent_playlist_id")
                    or ""
                )
            # daily / recommendation: evidence of exposure only; never preference
            allow_mem = bool(
                allow_create_memory
                and etype not in ("music.recommendation_seen",)
                and data_kind == "user_action"
                and action == "saved"
            )
            provenance: dict[str, Any] = {
                "provider": snapshot.provider,
                "collector_id": snapshot.data_type,
                "snapshot_id": snapshot.snapshot_id,
                "source_record_id": tid,
            }
            if playlist_id and action == "contains":
                provenance["playlist_id"] = playlist_id
            evidence.append(
                {
                    "evidence_id": f"ev_{secrets.token_hex(6)}",
                    "evidence_type": etype,
                    "subject_type": default_target,
                    "subject_id": tid,
                    "source": "music_acquisition",
                    "observed_at": snapshot.fetched_at,
                    "captured_at": imported_at,
                    # Contract fields (also mirrored into observation metadata on submit)
                    "domain": "music",
                    "source_type": "external_app",
                    "source_app": str(snapshot.provider or "netease"),
                    "source_record_id": tid,
                    "data_kind": data_kind,
                    "action": action,
                    "target_type": default_target,
                    "target_id": tid,
                    "occurred_at": snapshot.fetched_at,
                    "imported_at": imported_at,
                    "status": "observed",
                    "evidence_refs": [],
                    "title": title,
                    "artists": artists,
                    "playlist_id": playlist_id or None,
                    "provenance": provenance,
                    "policy": {
                        "allow_create_memory": allow_mem,
                        "note": (
                            "recommendation_seen / single play must not become long-term like; "
                            "saved is observed user_action only until confirmed"
                        ),
                    },
                }
            )
        return evidence

    def enqueue_outbox(self, items: list[dict[str, Any]]) -> str:
        oid = f"out_{secrets.token_hex(6)}"
        self._host.local_storage.write_json(
            f"memory/outbox/{oid}.json",
            {"outbox_id": oid, "items": items, "status": "pending"},
        )
        return oid
