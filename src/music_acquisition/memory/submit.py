# -*- coding: utf-8 -*-
"""Submit evidence outbox → Mnemosyne observations (idempotent best-effort)."""

from __future__ import annotations

from typing import Any

from music_acquisition.contracts.host_runtime import HostRuntime
from music_acquisition.memory.mnemosyne_client import MnemosyneHttpClient
from music_acquisition.memory.outbox import EvidenceOutbox


def _observation_metadata(item: dict[str, Any], *, outbox_id: str) -> dict[str, Any]:
    """Flatten contract fields onto observation metadata for domain gate / provenance."""
    return {
        "domain": item.get("domain") or "music",
        "source_type": item.get("source_type") or "external_app",
        "source_app": item.get("source_app") or "netease",
        "source_record_id": item.get("source_record_id") or item.get("subject_id") or "",
        "data_kind": item.get("data_kind") or "catalog",
        "action": item.get("action") or "",
        "target_type": item.get("target_type") or item.get("subject_type") or "track",
        "target_id": item.get("target_id") or item.get("subject_id") or "",
        "occurred_at": item.get("occurred_at") or item.get("observed_at") or "",
        "imported_at": item.get("imported_at") or item.get("captured_at") or "",
        "status": item.get("status") or "observed",
        "evidence_refs": list(item.get("evidence_refs") or []),
        "music_evidence": item,
        "idempotency_key": f"music:{outbox_id}:{item.get('evidence_id')}",
        "policy": item.get("policy") or {},
    }


class EvidenceSubmitter:
    def __init__(
        self,
        host: HostRuntime,
        client: MnemosyneHttpClient | None,
        outbox: EvidenceOutbox,
    ) -> None:
        self._host = host
        self._client = client
        self._outbox = outbox

    def submit_pending(self) -> dict[str, Any]:
        if not self._client:
            return {
                "ok": False,
                "error": "mnemosyne_not_configured",
                "pending": len(self._outbox.list_pending()),
            }
        results = []
        for batch in self._outbox.list_pending():
            oid = batch["outbox_id"]
            ok_all = True
            for item in batch.get("items") or []:
                # Never auto-create preference memory for recommendation_seen
                content = (
                    f"[music:{item.get('evidence_type')}|{item.get('data_kind')}|{item.get('action')}] "
                    f"target={item.get('target_type')}:{item.get('subject_id')} "
                    f"snapshot={item.get('provenance', {}).get('snapshot_id')}"
                )
                meta = _observation_metadata(item, outbox_id=oid)
                resp = self._client.observe(
                    {
                        "source_kind": "system",
                        "content": content,
                        "producer": "music_acquisition",
                        "occurred_at": meta.get("occurred_at") or None,
                        "idempotency_key": meta.get("idempotency_key"),
                        "metadata": meta,
                    }
                )
                if resp.get("ok") is False or resp.get("error"):
                    ok_all = False
                    break
            if ok_all:
                self._outbox.mark(oid, "submitted", submitted_at=self._host.clock.now_iso())
                results.append({"outbox_id": oid, "status": "submitted"})
            else:
                attempts = int(batch.get("attempts") or 0) + 1
                self._outbox.mark(oid, "pending", attempts=attempts, last_error="submit_failed")
                results.append({"outbox_id": oid, "status": "retry"})
        return {"ok": True, "results": results}
