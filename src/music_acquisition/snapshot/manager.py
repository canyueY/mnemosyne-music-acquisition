# -*- coding: utf-8 -*-
"""Snapshot Manager — complete wins over partial; hash + TTL metadata."""

from __future__ import annotations

import secrets
from urllib.parse import quote
from typing import Any

from music_acquisition.contracts.host_runtime import HostRuntime
from music_acquisition.domain.models import MusicSnapshot
from music_acquisition.snapshot.diff import compute_track_diff
from music_acquisition.snapshot.hashing import snapshot_hash


class SnapshotManager:
    def __init__(self, host: HostRuntime) -> None:
        self._host = host

    def _key(self, data_type: str, subject_id: str, local_date: str | None = None) -> str:
        data_type, subject_id = quote(data_type, safe=""), quote(subject_id, safe="")
        if local_date:
            from datetime import date
            date.fromisoformat(local_date)
        if local_date:
            return f"snapshots/{data_type}/{subject_id}/{local_date}.json"
        return f"snapshots/{data_type}/{subject_id}/current.json"

    def load(
        self, data_type: str, subject_id: str, local_date: str | None = None
    ) -> MusicSnapshot | None:
        raw = self._host.local_storage.read_json(self._key(data_type, subject_id, local_date))
        return MusicSnapshot.model_validate(raw) if raw else None

    def commit(
        self,
        *,
        owner_user_id: str,
        data_type: str,
        subject_id: str,
        records: list[Any],
        complete: bool,
        expected_count: int | None = None,
        local_date: str | None = None,
        status: str | None = None,
    ) -> MusicSnapshot | None:
        """Partial never overwrites a complete current snapshot."""
        existing = self.load(data_type, subject_id, local_date)
        dumped = [r.model_dump(mode="json") if hasattr(r, "model_dump") else r for r in records]
        def identity(r):
            if not isinstance(r, dict):
                return None
            for key in ("provider_track_id", "track_id", "provider_artist_id", "artist_id", "provider_playlist_id", "playlist_id", "subject_id"):
                if r.get(key) is not None:
                    # History can include repeat plays with distinct event timestamps.
                    return (r.get("provider", "netease"), key, str(r[key]), r.get("parent_playlist_id", ""), (r.get("played_at") or r.get("occurred_at")) if data_type == "playback_history" else "")
            return None
        ids = [identity(r) for r in dumped]
        valid = (expected_count is None or expected_count == len(dumped)) and all(ids) and len(set(ids)) == len(ids)
        complete = bool(complete and valid)
        status = "complete" if complete else "partial"
        if existing and existing.complete and complete and existing.snapshot_hash == snapshot_hash(dumped):
            return existing
        if existing and existing.complete and not complete:
            self._host.logger.warning(
                "reject_partial_overwrite",
                data_type=data_type,
                subject_id=subject_id,
                existing_snapshot=existing.snapshot_id,
            )
            # store partial aside
            aside = self._build(
                owner_user_id=owner_user_id,
                data_type=data_type,
                subject_id=subject_id,
                records=records,
                complete=False,
                expected_count=expected_count,
                local_date=local_date,
                status="partial",
            )
            self._host.local_storage.write_json(
                f"snapshots/{data_type}/{subject_id}/partial_{aside.snapshot_id}.json",
                aside.model_dump(mode="json"),
            )
            return None

        snap = self._build(
            owner_user_id=owner_user_id,
            data_type=data_type,
            subject_id=subject_id,
            records=records,
            complete=complete,
            expected_count=expected_count,
            local_date=local_date,
            status=status or ("complete" if complete else "partial"),
        )
        if existing and existing.complete:
            self._host.local_storage.write_json(f"snapshots/history/{existing.snapshot_id}.json", existing.model_dump(mode="json"))
        self._host.local_storage.write_json(
            self._key(data_type, subject_id, local_date),
            snap.model_dump(mode="json"),
        )
        if existing and existing.complete and snap.complete:
            diff = self.diff_against_previous(previous=existing, current=snap)
            self._host.local_storage.write_json(f"diffs/{diff.diff_id}.json", diff.model_dump(mode="json"))
        return snap

    def diff_against_previous(
        self,
        *,
        previous: MusicSnapshot,
        current: MusicSnapshot,
    ):
        return compute_track_diff(
            subject_id=current.subject_id,
            data_type=current.data_type,
            previous_snapshot_id=previous.snapshot_id,
            current_snapshot_id=current.snapshot_id,
            previous_hash=previous.snapshot_hash,
            current_hash=current.snapshot_hash,
            previous_records=previous.records,
            current_records=current.records,
            generated_at=self._host.clock.now_iso(),
        )

    def _build(
        self,
        *,
        owner_user_id: str,
        data_type: str,
        subject_id: str,
        records: list[Any],
        complete: bool,
        expected_count: int | None,
        local_date: str | None,
        status: str,
    ) -> MusicSnapshot:
        dumped = [
            r.model_dump(mode="json") if hasattr(r, "model_dump") else r for r in records
        ]
        expires_at = None
        if local_date:
            from datetime import datetime, date, time, timedelta
            from zoneinfo import ZoneInfo
            expires_at = datetime.combine(date.fromisoformat(local_date) + timedelta(days=1), time.min, ZoneInfo(self._host.clock.timezone())).isoformat()
        return MusicSnapshot(
            snapshot_id=f"snap_{secrets.token_hex(8)}",
            owner_user_id=owner_user_id,
            data_type=data_type,
            subject_id=subject_id,
            local_date=local_date,
            timezone=self._host.clock.timezone(),
            fetched_at=self._host.clock.now_iso(),
            expires_at=expires_at,
            status=status,  # type: ignore[arg-type]
            complete=complete,
            expected_count=expected_count,
            actual_count=len(dumped),
            snapshot_hash=snapshot_hash(dumped),
            records=dumped,
        )
