# -*- coding: utf-8 -*-
"""Evidence outbox — durable pending submit when Mnemosyne unavailable."""

from __future__ import annotations

import secrets
from typing import Any

from music_acquisition.contracts.host_runtime import HostRuntime


class EvidenceOutbox:
    def __init__(self, host: HostRuntime) -> None:
        self._host = host

    def enqueue(self, items: list[dict[str, Any]], *, idempotency_key: str = "") -> str:
        oid = f"out_{secrets.token_hex(6)}"
        payload = {
            "outbox_id": oid,
            "idempotency_key": idempotency_key or oid,
            "items": items,
            "status": "pending",
            "created_at": self._host.clock.now_iso(),
            "attempts": 0,
        }
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
            if raw and raw.get("status") == "pending":
                out.append(raw)
        return out

    def mark(self, outbox_id: str, status: str, **extra: Any) -> None:
        raw = self._host.local_storage.read_json(f"memory/outbox/{outbox_id}.json")
        if not raw:
            return
        raw["status"] = status
        raw.update(extra)
        self._host.local_storage.write_json(f"memory/outbox/{outbox_id}.json", raw)
