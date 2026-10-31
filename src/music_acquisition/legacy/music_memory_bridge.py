# -*- coding: utf-8 -*-
"""Legacy Compatibility — read old music_memory profile as evidence candidates.

Does NOT write long-term authority. Mnemosyne remains sole LT.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_legacy_profile(path: str | Path) -> dict[str, Any] | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def profile_to_evidence_candidates(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert legacy taste rows → evidence dicts for Outbox (explicit notes higher)."""
    out: list[dict[str, Any]] = []
    tracks = profile.get("tracks") if isinstance(profile.get("tracks"), dict) else {}
    for key, row in list(tracks.items())[:200]:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "evidence_id": f"legacy_{key}",
                "evidence_type": "music.played",
                "subject_type": "track",
                "subject_id": str(row.get("song_id") or key),
                "source": "legacy_music_memory",
                "observed_at": str(row.get("last_at") or ""),
                "provenance": {
                    "provider": "netease",
                    "collector_id": "legacy_profile",
                    "source_record_id": str(key),
                },
                "policy": {"allow_create_memory": False, "legacy_migration": True},
            }
        )
    notes = profile.get("taste_notes") if isinstance(profile.get("taste_notes"), list) else []
    for i, note in enumerate(notes[:50]):
        text = note if isinstance(note, str) else str((note or {}).get("text") if isinstance(note, dict) else note)
        out.append(
            {
                "evidence_id": f"legacy_note_{i}",
                "evidence_type": "music.explicit_preference",
                "subject_type": "taste",
                "subject_id": f"note:{i}",
                "source": "legacy_music_memory",
                "context": {"text": text},
                "provenance": {"provider": "user", "collector_id": "legacy_taste_note"},
                "policy": {"allow_create_memory": True, "legacy_migration": True},
            }
        )
    return out
