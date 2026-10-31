# -*- coding: utf-8 -*-
"""Evidence outbox — durable pending submit with per-item status + file lock."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from music_acquisition.contracts.host_runtime import HostRuntime
from music_acquisition.util.file_lock import FileLock


def _normalize_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
        row = dict(it)
        row.setdefault("item_status", "pending")
        row.setdefault("item_attempts", 0)
        row.setdefault("item_last_error", "")
        out.append(row)
    return out


class EvidenceOutbox:
    def __init__(self, host: HostRuntime) -> None:
        self._host = host

    def _root(self) -> Path:
        return Path(self._host.local_storage.root()) / "memory" / "outbox"

    def _lock(self) -> FileLock:
        return FileLock(self._root() / "_outbox.lock", timeout_sec=45.0)

    def enqueue(self, items: list[dict[str, Any]], *, idempotency_key: str = "") -> str:
        oid = f"out_{secrets.token_hex(6)}"
        payload = {
            "outbox_id": oid,
            "idempotency_key": idempotency_key or oid,
            "items": _normalize_items(items),
            "status": "pending",
            "created_at": self._host.clock.now_iso(),
            "attempts": 0,
            "item_stats": {"pending": len(items), "submitted": 0, "failed": 0},
        }
        with self._lock():
            index = self._host.local_storage.read_json("memory/outbox/_index.json") or {"ids": []}
            if idempotency_key:
                for existing_id in index.get("ids", []):
                    previous = self.load(existing_id)
                    if previous and previous.get("idempotency_key") == idempotency_key:
                        return existing_id
            self._host.local_storage.write_json(f"memory/outbox/{oid}.json", payload)
            index = self._host.local_storage.read_json("memory/outbox/_index.json") or {"ids": []}
            ids = list(index.get("ids") or [])
            if oid not in ids:
                ids.append(oid)
            self._host.local_storage.write_json("memory/outbox/_index.json", {"ids": ids})
        return oid

    def list_pending(self) -> list[dict[str, Any]]:
        index = self._host.local_storage.read_json("memory/outbox/_index.json") or {"ids": []}
        out = []
        for oid in index.get("ids") or []:
            raw = self._host.local_storage.read_json(f"memory/outbox/{oid}.json")
            if not raw:
                continue
            status = str(raw.get("status") or "")
            if status in ("pending", "partial"):
                out.append(raw)
        return out

    def load(self, outbox_id: str) -> dict[str, Any] | None:
        raw = self._host.local_storage.read_json(f"memory/outbox/{outbox_id}.json")
        return raw if isinstance(raw, dict) else None

    def mark(self, outbox_id: str, status: str, **extra: Any) -> None:
        with self._lock():
            raw = self._host.local_storage.read_json(f"memory/outbox/{outbox_id}.json")
            if not raw:
                return
            raw["status"] = status
            raw.update(extra)
            self._host.local_storage.write_json(f"memory/outbox/{outbox_id}.json", raw)

    def update_items(
        self,
        outbox_id: str,
        items: list[dict[str, Any]],
        *,
        batch_status: str | None = None,
        attempts: int | None = None,
        last_error: str = "",
        next_retry_at: str = "",
    ) -> dict[str, Any]:
        """Atomically rewrite item rows + recompute batch status."""
        with self._lock():
            raw = self._host.local_storage.read_json(f"memory/outbox/{outbox_id}.json")
            if not raw:
                return {"ok": False, "error": "not_found"}
            raw["items"] = items
            pending = sum(1 for i in items if str(i.get("item_status") or "") == "pending")
            submitted = sum(1 for i in items if str(i.get("item_status") or "") == "submitted")
            failed = sum(1 for i in items if str(i.get("item_status") or "") == "failed")
            raw["item_stats"] = {
                "pending": pending,
                "submitted": submitted,
                "failed": failed,
                "total": len(items),
            }
            if batch_status:
                raw["status"] = batch_status
            elif pending == 0 and failed == 0:
                raw["status"] = "submitted"
            elif pending == 0 and failed > 0 and submitted > 0:
                raw["status"] = "partial"
            elif pending == 0 and failed > 0:
                raw["status"] = "failed"
            else:
                raw["status"] = "partial" if submitted else "pending"
            if attempts is not None:
                raw["attempts"] = attempts
            if last_error:
                raw["last_error"] = last_error
            if next_retry_at:
                raw["next_retry_at"] = next_retry_at
            raw["updated_at"] = self._host.clock.now_iso()
            self._host.local_storage.write_json(f"memory/outbox/{outbox_id}.json", raw)
            return {"ok": True, **raw}
