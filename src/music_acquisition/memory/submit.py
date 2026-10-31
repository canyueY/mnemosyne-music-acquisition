# -*- coding: utf-8 -*-
"""Submit evidence outbox → Mnemosyne observations (per-item + backoff)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from music_acquisition.contracts.host_runtime import HostRuntime
from music_acquisition.memory.mnemosyne_client import MnemosyneHttpClient
from music_acquisition.memory.outbox import EvidenceOutbox
from music_acquisition.sync.retry import backoff_delay, should_retry


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


def _iso_after(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _due(batch: dict[str, Any]) -> bool:
    nxt = str(batch.get("next_retry_at") or "").strip()
    if not nxt:
        return True
    try:
        # accept Z suffix
        ts = nxt.replace("Z", "+00:00")
        when = datetime.fromisoformat(ts)
        return datetime.now(timezone.utc) >= when
    except Exception:
        return True


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

    def submit_pending(self, *, force: bool = False) -> dict[str, Any]:
        if not self._client:
            return {
                "ok": False,
                "error": "mnemosyne_not_configured",
                "pending": len(self._outbox.list_pending()),
            }
        results = []
        for batch in self._outbox.list_pending():
            if not force and not _due(batch):
                results.append(
                    {
                        "outbox_id": batch.get("outbox_id"),
                        "status": "deferred",
                        "next_retry_at": batch.get("next_retry_at"),
                    }
                )
                continue
            oid = str(batch["outbox_id"])
            items = [dict(x) for x in (batch.get("items") or [])]
            submitted_now = 0
            failed_now = 0
            last_error = ""
            for item in items:
                st = str(item.get("item_status") or "pending")
                if st == "submitted":
                    continue
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
                err = ""
                if resp.get("ok") is False or resp.get("error"):
                    err = str(resp.get("error") or resp.get("detail") or "submit_failed")
                    item["item_attempts"] = int(item.get("item_attempts") or 0) + 1
                    item["item_last_error"] = err
                    last_error = err
                    # permanent? mark failed; else keep pending for retry
                    if should_retry(err):
                        item["item_status"] = "pending"
                    else:
                        # treat unknown HTTP as retryable unless auth
                        if str(err).startswith("http_5") or err in ("network", "submit_failed"):
                            item["item_status"] = "pending"
                        elif "auth" in err.lower() or "unauthorized" in err.lower():
                            item["item_status"] = "failed"
                        else:
                            item["item_status"] = "pending"
                    if item["item_status"] == "failed":
                        failed_now += 1
                    continue
                item["item_status"] = "submitted"
                item["item_submitted_at"] = self._host.clock.now_iso()
                item["item_last_error"] = ""
                submitted_now += 1

            attempts = int(batch.get("attempts") or 0) + 1
            still_pending = any(str(i.get("item_status")) == "pending" for i in items)
            next_retry = ""
            if still_pending:
                delay = backoff_delay(attempts)
                next_retry = _iso_after(delay)
            updated = self._outbox.update_items(
                oid,
                items,
                attempts=attempts,
                last_error=last_error,
                next_retry_at=next_retry,
            )
            results.append(
                {
                    "outbox_id": oid,
                    "status": updated.get("status"),
                    "submitted_now": submitted_now,
                    "failed_now": failed_now,
                    "item_stats": updated.get("item_stats"),
                    "next_retry_at": next_retry or None,
                }
            )
        ok = not any(r.get("status") == "failed" for r in results)
        return {"ok": ok, "results": results, "per_item": True}
