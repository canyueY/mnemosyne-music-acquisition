# -*- coding: utf-8 -*-
"""Open Memory Bank backend interface — Mnemosyne default, pluggable alternatives.

Music acquisition never opens ``memory.db`` directly. Long-term cognition goes through
a ``MemoryBankBackend``. Default: Mnemosyne HTTP. Alternate: local JSON mirror under
Memory Bank (offline / other vendors can implement the same Protocol).
"""

from __future__ import annotations

import json
import secrets
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from music_acquisition.memory.mnemosyne_client import MnemosyneHttpClient

USER_ANCHOR_IDEMPOTENCY = "music:user_anchor:self"


class MemoryBankBackend(ABC):
    """Vendor-neutral surface for music → long-term memory operations."""

    kind: str = "abstract"

    @abstractmethod
    def health(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def propose_memory(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    @abstractmethod
    def update_memory(self, memory_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    @abstractmethod
    def link_relation(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    @abstractmethod
    def feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    @abstractmethod
    def search(self, query: str, *, limit: int = 10, domain: str | None = None) -> dict[str, Any]:
        ...

    @abstractmethod
    def observe(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def list_relations(self, memory_id: str) -> dict[str, Any]:
        return {"ok": False, "error": "list_relations_unsupported", "items": []}

    def revoke_relations(
        self,
        *,
        from_id: str = "",
        to_id: str = "",
        relation: str = "likes",
    ) -> dict[str, Any]:
        """Best-effort soft/hard revoke of matching edges. Optional on backends."""
        return {"ok": False, "error": "revoke_relations_unsupported", "removed": 0}

    def ensure_user_anchor(self, *, subject_key: str = "self", display: str = "") -> dict[str, Any]:
        """Idempotent User endpoint memory for saves/listened_to/likes."""
        content = display or f"[music:user_anchor] subject={subject_key}"
        resp = self.propose_memory(
            {
                "type": "semantic",
                "status": "active",
                "content": content,
                "confidence": 0.9,
                "idempotency_key": f"{USER_ANCHOR_IDEMPOTENCY}:{subject_key}",
                "metadata": {
                    "domain": "music",
                    "role": "user_anchor",
                    "subject_key": subject_key,
                    "data_kind": "identity",
                    "status": "confirmed",
                },
            }
        )
        mid = str(resp.get("id") or (resp.get("memory") or {}).get("id") or "")
        return {"ok": bool(mid) or resp.get("ok") is not False, "id": mid, "response": resp}

    def register_domain_pack(
        self, pack: dict[str, Any] | None = None, *, pack_path: str = ""
    ) -> dict[str, Any]:
        return {"ok": False, "error": "register_domain_unsupported", "kind": self.kind}


class MnemosyneBankBackend(MemoryBankBackend):
    """Default: Mnemosyne Service Contract over HTTP (+ optional local audit dir)."""

    kind = "mnemosyne"

    def __init__(
        self,
        client: MnemosyneHttpClient,
        *,
        audit_root: Path | str | None = None,
    ) -> None:
        self._client = client
        self._audit = Path(audit_root) if audit_root else None
        if self._audit:
            self._audit.mkdir(parents=True, exist_ok=True)

    def health(self) -> dict[str, Any]:
        h = self._client.health()
        return {"ok": h.get("ok", True) is not False and not h.get("error"), "kind": self.kind, "raw": h}

    def propose_memory(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._client.propose_memory(payload)

    def update_memory(self, memory_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._client.update_memory(memory_id, payload)

    def link_relation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._client.link_relation(payload)

    def feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._client.feedback(payload)

    def search(self, query: str, *, limit: int = 10, domain: str | None = None) -> dict[str, Any]:
        return self._client.search(query, limit=limit, domain=domain)

    def observe(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._client.observe(payload)

    def register_domain_pack(
        self, pack: dict[str, Any] | None = None, *, pack_path: str = ""
    ) -> dict[str, Any]:
        return self._client.register_domain_pack(pack, pack_path=pack_path)

    def write_audit(self, name: str, payload: dict[str, Any]) -> None:
        if not self._audit:
            return
        path = self._audit / f"{name}_{secrets.token_hex(4)}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_relations(self, memory_id: str) -> dict[str, Any]:
        return self._client.list_relations(memory_id)

    def revoke_relations(
        self,
        *,
        from_id: str = "",
        to_id: str = "",
        relation: str = "likes",
    ) -> dict[str, Any]:
        # Mnemosyne has no public DELETE relation yet — soft-note via observe on caller.
        listed = self.list_relations(to_id or from_id) if (to_id or from_id) else {"items": []}
        items = listed.get("items") or listed.get("relations") or []
        matched = [
            r
            for r in items
            if isinstance(r, dict)
            and str(r.get("relation") or "") == relation
            and (not from_id or str(r.get("from_id") or "") == from_id)
            and (not to_id or str(r.get("to_id") or "") == to_id)
        ]
        return {
            "ok": True,
            "removed": 0,
            "soft": True,
            "matched": matched,
            "note": "mnemosyne_no_delete_relation; memory invalidated by caller",
        }


class LocalFileBankBackend(MemoryBankBackend):
    """Offline / alternate bank: JSON store under Memory Bank.

    Other vendors can implement ``MemoryBankBackend`` the same way, or wrap this
    mirror as a sync target.
    """

    kind = "local_file"

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in ("memories", "relations", "feedback", "observations", "audit"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self._index = self.root / "_index.json"
        if not self._index.is_file():
            self._write_index({"memories": {}, "by_idempotency": {}})

    def _read_index(self) -> dict[str, Any]:
        return json.loads(self._index.read_text(encoding="utf-8"))

    def _write_index(self, data: dict[str, Any]) -> None:
        self._index.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def health(self) -> dict[str, Any]:
        return {"ok": True, "kind": self.kind, "root": str(self.root)}

    def propose_memory(self, payload: dict[str, Any]) -> dict[str, Any]:
        idx = self._read_index()
        idem = str(payload.get("idempotency_key") or "")
        if idem and idem in (idx.get("by_idempotency") or {}):
            mid = idx["by_idempotency"][idem]
            existing = json.loads((self.root / "memories" / f"{mid}.json").read_text(encoding="utf-8"))
            return {"ok": True, "id": mid, "reused": True, **existing}
        mid = f"lf_{secrets.token_hex(8)}"
        doc = {"id": mid, **payload}
        (self.root / "memories" / f"{mid}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        idx.setdefault("memories", {})[mid] = {"idempotency_key": idem}
        if idem:
            idx.setdefault("by_idempotency", {})[idem] = mid
        self._write_index(idx)
        return {"ok": True, "id": mid, **doc}

    def update_memory(self, memory_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        path = self.root / "memories" / f"{memory_id}.json"
        if not path.is_file():
            return {"ok": False, "error": "not_found", "id": memory_id}
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc.update({k: v for k, v in payload.items() if v is not None})
        if isinstance(payload.get("metadata"), dict):
            meta = dict(doc.get("metadata") or {})
            meta.update(payload["metadata"])
            doc["metadata"] = meta
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "id": memory_id, **doc}

    def link_relation(self, payload: dict[str, Any]) -> dict[str, Any]:
        rid = f"rel_{secrets.token_hex(6)}"
        doc = {"id": rid, **payload}
        (self.root / "relations" / f"{rid}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"ok": True, **doc}

    def feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        fid = f"fb_{secrets.token_hex(6)}"
        doc = {"id": fid, **payload}
        (self.root / "feedback" / f"{fid}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"ok": True, **doc}

    def search(self, query: str, *, limit: int = 10, domain: str | None = None) -> dict[str, Any]:
        hits = []
        q = (query or "").lower()
        for path in sorted((self.root / "memories").glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            meta = doc.get("metadata") or {}
            if domain and str(meta.get("domain") or "") != domain:
                continue
            content = str(doc.get("content") or "")
            if q and q not in content.lower() and q not in json.dumps(meta, ensure_ascii=False).lower():
                continue
            hits.append(doc)
            if len(hits) >= limit:
                break
        return {"ok": True, "items": hits, "count": len(hits)}

    def observe(self, payload: dict[str, Any]) -> dict[str, Any]:
        oid = f"obs_{secrets.token_hex(6)}"
        doc = {"id": oid, **payload}
        (self.root / "observations" / f"{oid}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"ok": True, **doc}

    def list_relations(self, memory_id: str) -> dict[str, Any]:
        items = []
        for path in sorted((self.root / "relations").glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            if str(doc.get("from_id") or "") == memory_id or str(doc.get("to_id") or "") == memory_id:
                if not doc.get("revoked"):
                    items.append(doc)
        return {"ok": True, "items": items}

    def revoke_relations(
        self,
        *,
        from_id: str = "",
        to_id: str = "",
        relation: str = "likes",
    ) -> dict[str, Any]:
        removed = 0
        matched = []
        for path in list((self.root / "relations").glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            if str(doc.get("relation") or "") != relation:
                continue
            if from_id and str(doc.get("from_id") or "") != from_id:
                continue
            if to_id and str(doc.get("to_id") or "") != to_id:
                continue
            doc["revoked"] = True
            doc["revoked_at"] = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            removed += 1
            matched.append(doc.get("id"))
        return {"ok": True, "removed": removed, "matched": matched, "soft": False}


def open_memory_bank(
    kind: str = "mnemosyne",
    *,
    mnemosyne_base_url: str = "",
    mnemosyne_token: str = "",
    local_root: Path | str | None = None,
    audit_root: Path | str | None = None,
    client: MnemosyneHttpClient | None = None,
) -> MemoryBankBackend:
    """Factory for pluggable banks. ``kind``: mnemosyne | local_file | dual."""
    k = (kind or "mnemosyne").strip().lower()
    if k in ("local", "local_file", "file"):
        if not local_root:
            raise ValueError("local_file backend requires local_root")
        return LocalFileBankBackend(local_root)
    if k == "dual":
        # Mnemosyne primary + local mirror for audit / offline fallback writes
        if not client and not mnemosyne_base_url:
            if not local_root:
                raise ValueError("dual requires mnemosyne or local_root")
            return LocalFileBankBackend(local_root)
        primary = MnemosyneBankBackend(
            client or MnemosyneHttpClient(mnemosyne_base_url, mnemosyne_token),
            audit_root=audit_root or local_root,
        )
        return primary
    # default mnemosyne
    if client is None:
        if not mnemosyne_base_url:
            # degrade to local if possible
            if local_root:
                return LocalFileBankBackend(local_root)
            raise ValueError("mnemosyne backend requires base_url or client")
        client = MnemosyneHttpClient(mnemosyne_base_url, mnemosyne_token)
    return MnemosyneBankBackend(client, audit_root=audit_root)
