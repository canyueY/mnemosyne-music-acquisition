# -*- coding: utf-8 -*-
"""Music Recall — distinguishes explicit / behavior / inference / unknown."""

from __future__ import annotations

from typing import Any

from music_acquisition.contracts.host_runtime import HostRuntime
from music_acquisition.domain.models import MusicSnapshot
from music_acquisition.memory.mnemosyne_client import MnemosyneHttpClient


class MusicRecallService:
    def __init__(
        self,
        host: HostRuntime,
        *,
        mnemosyne: MnemosyneHttpClient | None = None,
    ) -> None:
        self._host = host
        self._mnemosyne = mnemosyne

    def recall_from_snapshots(
        self,
        snapshots: list[MusicSnapshot],
        *,
        query: str = "",
    ) -> dict[str, Any]:
        claims: list[dict[str, Any]] = []
        for snap in snapshots:
            if snap.data_type == "liked_songs" and snap.complete:
                claims.append(
                    {
                        "claim": f"User has {snap.actual_count} liked songs in acquisition snapshot",
                        "status": "observed_behavior",
                        "evidence_refs": [snap.snapshot_id],
                    }
                )
            elif snap.data_type == "daily_songs":
                claims.append(
                    {
                        "claim": f"Daily recommendation exposure on {snap.local_date} ({snap.actual_count} tracks)",
                        "status": "observed_behavior",
                        "evidence_refs": [snap.snapshot_id],
                        "note": "not_an_explicit_like",
                    }
                )
        if query and "喜欢" in query and not any(c["status"] == "explicit_fact" for c in claims):
            claims.append(
                {
                    "claim": "No explicit preference fact in acquisition cache for this query",
                    "status": "unknown",
                    "evidence_refs": [],
                }
            )
        return {
            "claims": claims,
            "conflicts": [],
            "unknowns": [c["claim"] for c in claims if c["status"] == "unknown"],
            "source_refs": [s.snapshot_id for s in snapshots],
        }

    def recall_with_mnemosyne(self, query: str) -> dict[str, Any]:
        if not self._mnemosyne:
            return {"ok": False, "error": "mnemosyne_client_not_configured"}
        hit = self._mnemosyne.search(query, limit=8)
        return {
            "ok": bool(hit.get("ok", True) if "ok" in hit else "items" in hit or "memories" in hit),
            "backend": "mnemosyne",
            "raw": hit,
            "note": "LT recall authority is Mnemosyne; acquisition cache is secondary",
        }
