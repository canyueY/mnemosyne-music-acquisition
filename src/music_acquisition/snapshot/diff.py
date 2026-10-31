# -*- coding: utf-8 -*-
from __future__ import annotations

import secrets
from typing import Any

from music_acquisition.domain.models import MusicDiff, MusicDiffItem


def _track_id(rec: Any) -> str:
    if isinstance(rec, dict):
        return str(rec.get("provider_track_id") or rec.get("track_id") or "")
    return str(getattr(rec, "provider_track_id", None) or getattr(rec, "track_id", "") or "")


def compute_track_diff(
    *,
    subject_id: str,
    data_type: str,
    previous_snapshot_id: str,
    current_snapshot_id: str,
    previous_hash: str,
    current_hash: str,
    previous_records: list[Any],
    current_records: list[Any],
    generated_at: str,
) -> MusicDiff:
    prev_ids = {_track_id(r) for r in previous_records if _track_id(r)}
    curr_ids = {_track_id(r) for r in current_records if _track_id(r)}
    changes: list[MusicDiffItem] = []
    for tid in sorted(curr_ids - prev_ids):
        changes.append(
            MusicDiffItem(change_type="added", entity_type="track", provider_entity_id=tid)
        )
    for tid in sorted(prev_ids - curr_ids):
        changes.append(
            MusicDiffItem(
                change_type="removed",
                entity_type="track",
                provider_entity_id=tid,
                detail={"tombstone": True, "reason": "removed_from_source"},
            )
        )
    return MusicDiff(
        diff_id=f"diff_{secrets.token_hex(8)}",
        subject_id=subject_id,
        data_type=data_type,
        previous_snapshot_id=previous_snapshot_id,
        current_snapshot_id=current_snapshot_id,
        previous_hash=previous_hash,
        current_hash=current_hash,
        changes=changes,
        generated_at=generated_at,
    )
