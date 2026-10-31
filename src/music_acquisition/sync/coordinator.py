# -*- coding: utf-8 -*-
"""Single Sync Coordinator for manual / scheduled / recovery / retry."""

from __future__ import annotations

import secrets
from typing import Any, Awaitable, Callable

from music_acquisition.contracts.host_runtime import HostRuntime
from music_acquisition.contracts.source_adapter import MusicSourceAdapter
from music_acquisition.domain.models import SyncRequest, SyncRun, SyncStatus
from music_acquisition.governance.scopes import PermissionGate
from music_acquisition.snapshot.manager import SnapshotManager
from music_acquisition.sync.recovery import clear_pending, mark_pending

CollectorFn = Callable[[], Awaitable[Any]]


class SyncCoordinator:
    def __init__(
        self,
        host: HostRuntime,
        source: MusicSourceAdapter,
        snapshots: SnapshotManager,
        gate: PermissionGate,
        *,
        node_id: str = "local-node",
    ) -> None:
        self._host = host
        self._source = source
        self._snapshots = snapshots
        self._gate = gate
        self._node_id = node_id
        self._locks: set[str] = set()
        self._runs: dict[str, SyncRun] = {}

    async def sync_now(self, request: SyncRequest) -> SyncRun:
        run = SyncRun(
            run_id=f"run_{secrets.token_hex(8)}",
            node_id=self._node_id,
            owner_user_id=request.owner_user_id,
            trigger=request.trigger,
            status="queued",
            collectors=list(request.collectors)
            or ["liked_songs", "playlists", "daily_songs", "playback_history"],
            started_at=self._host.clock.now_iso(),
        )
        self._runs[run.run_id] = run
        mark_pending(self._host, run.run_id)
        run.status = "checking_auth"
        auth = await self._source.check_authorization()
        if auth.status not in ("valid", "authorized"):
            run.status = "auth_required"
            run.error_code = "LOGIN_REQUIRED"
            run.completed_at = self._host.clock.now_iso()
            clear_pending(self._host, run.run_id)
            return run

        subject_id = auth.subject_id or "unknown"
        run.status = "fetching"
        warnings: list[str] = []
        snapshot_ids: list[str] = []

        collectors = {
            "liked_songs": self._source.get_liked_songs,
            "playlists": self._source.get_playlists,
            "daily_songs": self._source.get_daily_songs,
            "playback_history": self._source.get_playback_history,
            "profile": self._source.get_profile,
        }
        fav = getattr(self._source, "get_favorite_artists", None)
        if callable(fav):
            collectors["favorite_artists"] = fav

        for cid in run.collectors:
            if cid not in collectors:
                warnings.append(f"unknown_collector:{cid}")
                continue
            if not self._gate.allow_collector(request.owner_user_id, cid):
                warnings.append(f"scope_denied:{cid}")
                continue
            lock = f"{self._node_id}:{request.owner_user_id}:netease:{cid}:{subject_id}"
            if lock in self._locks:
                warnings.append(f"lock_busy:{cid}")
                continue
            self._locks.add(lock)
            try:
                result = await collectors[cid]()
                local_date = None
                if cid == "daily_songs":
                    local_date = self._host.clock.now_iso()[:10]
                snap = self._snapshots.commit(
                    owner_user_id=request.owner_user_id,
                    data_type=cid,
                    subject_id=subject_id,
                    records=list(result.records),
                    complete=bool(result.complete and result.status == "success"),
                    expected_count=result.expected_count,
                    local_date=local_date,
                )
                if snap:
                    snapshot_ids.append(snap.snapshot_id)
                elif result.status == "partial" or not result.complete:
                    warnings.append(f"partial_kept_previous:{cid}")
            except PermissionError as exc:
                run.status = "auth_required"
                run.error_code = str(exc)
                warnings.append(f"{cid}:{exc}")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{cid}:error:{exc}")
            finally:
                self._locks.discard(lock)

        run.warnings = warnings
        run.snapshot_ids = snapshot_ids
        run.completed_at = self._host.clock.now_iso()
        if run.error_code == "LOGIN_REQUIRED":
            pass
        elif any(w.startswith("partial_") or ":error:" in w for w in warnings) and snapshot_ids:
            run.status = "partial"
        elif snapshot_ids:
            run.status = "success"
        elif warnings and all(w.startswith("scope_denied:") for w in warnings):
            run.status = "failed"
            run.error_code = "SCOPE_DENIED"
        else:
            run.status = "failed" if warnings else "success"

        self._host.local_storage.write_json(
            f"sync/runs/{run.run_id}.json", run.model_dump(mode="json")
        )
        if run.status in ("success", "partial", "failed", "auth_required"):
            clear_pending(self._host, run.run_id)
        self._host.event_publisher.publish(
            "music.sync.completed",
            {"run_id": run.run_id, "status": run.status, "snapshots": len(snapshot_ids)},
        )
        return run

    def get_status(self, run_id: str | None = None) -> SyncStatus:
        if run_id and run_id in self._runs:
            r = self._runs[run_id]
            return SyncStatus(run_id=r.run_id, status=r.status, detail=r.error_code or "")
        return SyncStatus(
            status="idle" if not self._locks else "busy",
            active_locks=sorted(self._locks),
        )
