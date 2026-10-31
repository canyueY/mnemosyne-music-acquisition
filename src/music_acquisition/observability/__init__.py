# -*- coding: utf-8 -*-
"""Health / metrics stubs (Phase 3+)."""

def engine_health_payload(*, ok: bool, auth_status: str) -> dict:
    return {
        "engine": ok,
        "authorization": auth_status,
        "source_adapter": "unknown",
        "sync_queue": "idle",
        "snapshot": "unknown",
        "memory_submission": "unknown",
        "playback": "unsupported",
    }
