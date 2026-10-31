# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from music_acquisition.domain.models import MusicSnapshot


def _local_today(tz_name: str) -> str:
    """Best-effort local date; falls back to UTC date if tzdata missing."""
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
        return datetime.now(tz).date().isoformat()
    except Exception:
        # Windows without tzdata: use UTC calendar date as conservative fallback
        return datetime.now(timezone.utc).date().isoformat()


def mark_expired_if_needed(snap: MusicSnapshot, *, tz_name: str) -> MusicSnapshot:
    """Daily snapshots expire next local calendar day."""
    if snap.data_type != "daily_songs" or not snap.local_date:
        return snap
    today = _local_today(tz_name)
    if snap.local_date < today and snap.status != "expired":
        return snap.model_copy(update={"status": "expired", "expires_at": today})
    if not snap.expires_at and snap.local_date:
        try:
            d = date.fromisoformat(snap.local_date)
            exp = (d + timedelta(days=1)).isoformat()
            return snap.model_copy(update={"expires_at": exp})
        except ValueError:
            return snap
    return snap
