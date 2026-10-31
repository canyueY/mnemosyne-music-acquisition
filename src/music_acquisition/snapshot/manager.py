# -*- coding: utf-8 -*-
"""Snapshot Manager — complete wins over partial; hash + TTL metadata."""

from __future__ import annotations

import secrets
from typing import Any

from music_acquisition.contracts.host_runtime import HostRuntime
from music_acquisition.domain.models import MusicSnapshot
from music_acquisition.snapshot.diff import compute_track_diff
from music_acquisition.snapshot.hashing import snapshot_hash


class SnapshotManager:
    def __init__(self, host: HostRuntime) -> None:
        self._host = host

    def _key(self, data_type: str, subject_id: str, local_date: str | None = None) -> str:
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
        self._host.local_storage.write_json(
            self._key(data_type, subject_id, local_date),
            snap.model_dump(mode="json"),
        )
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
        return MusicSnapshot(
            snapshot_id=f"snap_{secrets.token_hex(8)}",
            owner_user_id=owner_user_id,
            data_type=data_type,
            subject_id=subject_id,
            local_date=local_date,
            timezone=self._host.clock.timezone(),
            fetched_at=self._host.clock.now_iso(),
            status=status,  # type: ignore[arg-type]
            complete=complete,
            expected_count=expected_count,
            actual_count=len(dumped),
            snapshot_hash=snapshot_hash(dumped),
            records=dumped,
        )
