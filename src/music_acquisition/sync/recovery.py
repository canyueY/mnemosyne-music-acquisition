# -*- coding: utf-8 -*-
"""Startup recovery — reload incomplete runs from local storage."""

from __future__ import annotations

from typing import Any

from music_acquisition.contracts.host_runtime import HostRuntime
from music_acquisition.domain.models import SyncRequest
from pathlib import Path
from music_acquisition.util.file_lock import FileLock


def list_pending_run_ids(host: HostRuntime) -> list[str]:
    raw = host.local_storage.read_json("sync/pending.json") or {"runs": []}
    return list(raw.get("runs") or [])


def mark_pending(host: HostRuntime, run_id: str) -> None:
    with FileLock(Path(host.local_storage.root()) / "sync" / ".pending.lock"):
        ids = set(list_pending_run_ids(host))
        ids.add(run_id)
        host.local_storage.write_json("sync/pending.json", {"runs": sorted(ids)})


def clear_pending(host: HostRuntime, run_id: str) -> None:
    with FileLock(Path(host.local_storage.root()) / "sync" / ".pending.lock"):
        ids = [x for x in list_pending_run_ids(host) if x != run_id]
        host.local_storage.write_json("sync/pending.json", {"runs": ids})


def startup_recovery_requests(host: HostRuntime, owner_user_id: str) -> list[SyncRequest]:
    """Build retry sync requests for interrupted work (collectors from last run files)."""
    out: list[SyncRequest] = []
    for rid in list_pending_run_ids(host):
        meta = host.local_storage.read_json(f"sync/runs/{rid}.json") or {}
        if meta.get("owner_user_id") != owner_user_id:
            continue
        if meta.get("next_retry_at"):
            from music_acquisition.util.runtime import parse_time
            if parse_time(meta["next_retry_at"]) > parse_time(host.clock.now_iso()):
                continue
        collectors = list(meta.get("collectors") or ["liked_songs", "daily_songs"])
        out.append(
            SyncRequest.model_validate({**meta.get("request", {}), "owner_user_id": owner_user_id,
                "collectors": collectors, "trigger": "startup_recovery", "recovery_run_id": rid})
        )
    return out


def load_run(host: HostRuntime, run_id: str) -> dict[str, Any] | None:
    return host.local_storage.read_json(f"sync/runs/{run_id}.json")
