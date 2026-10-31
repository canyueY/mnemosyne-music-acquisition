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


def mark_expired_if_needed(snap: MusicSnapshot, *, tz_name: str, today: str | None = None) -> MusicSnapshot:
    """Daily snapshots expire next local calendar day."""
    if snap.data_type not in {"daily_songs", "daily_playlists"} or not snap.local_date:
        return snap
    today = today or _local_today(tz_name)
    if snap.local_date < today and snap.status != "expired":
        return snap.model_copy(update={"status": "expired", "expires_at": today})
    if not snap.expires_at and snap.local_date:
        try:
            d = date.fromisoformat(snap.local_date)
            from zoneinfo import ZoneInfo
            from datetime import time
            exp = datetime.combine(d + timedelta(days=1), time.min, ZoneInfo(tz_name)).isoformat()
            return snap.model_copy(update={"expires_at": exp})
        except ValueError:
            return snap
    return snap
