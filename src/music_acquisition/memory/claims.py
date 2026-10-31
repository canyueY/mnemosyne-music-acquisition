# -*- coding: utf-8 -*-
"""Claim mapping layer (Mnemosyne 2.0-forward).

Current Node may not expose ``/api/v1/claims``. This module always produces a
Claim-shaped payload and persists it as:

1. ``POST /api/v1/claims`` when the route exists;
2. else a Memory with ``metadata.modality=preference`` + ``data_kind=claim``.

Music rules still apply: inferred ≠ confirmed; likes only via explicit confirm.
"""

from __future__ import annotations

import secrets
from typing import Any

from music_acquisition.memory.bank_backend import MemoryBankBackend
from music_acquisition.memory.mnemosyne_client import MnemosyneHttpClient


def build_preference_claim(
    *,
    target_type: str,
    target_id: str,
    title: str = "",
    epistemic: str = "inferred",
    modality: str = "preference",
    evidence_refs: list[str] | None = None,
    hypothesis: str = "",
    confidence: float = 0.6,
    status: str = "active",
) -> dict[str, Any]:
    tid = str(target_id)
    claim_id = f"claim_music_{epistemic}_{target_type}_{tid}_{secrets.token_hex(3)}"
    text = hypothesis or (
        f"用户{'确认喜欢' if epistemic == 'user_confirmed' else '可能偏好'}"
        f"{target_type}《{title or tid}》"
    )
    return {
        "claim_id": claim_id,
        "modality": modality,
        "domain": "music",
        "epistemic": epistemic,
        "status": status,
        "statement": text,
        "confidence": float(confidence),
        "subject": {"type": "user", "id": "self"},
        "object": {"type": target_type, "id": tid, "title": title},
        "evidence_refs": list(evidence_refs or []),
        "policy": {
            "must_not_write_likes": epistemic != "user_confirmed",
            "requires_verification_for_likes": True,
        },
        "schema_version": "music.claim.v1",
    }


def build_verification_record(
    *,
    claim_id: str,
    action: str,
    note: str = "",
) -> dict[str, Any]:
    return {
        "verification_id": f"ver_{secrets.token_hex(6)}",
        "claim_id": claim_id,
        "action": action,  # confirm | reject | unconfirm
        "note": note,
        "actor": "user",
        "schema_version": "music.verification.v1",
    }


class ClaimBridge:
    """Submit Claim payloads via native Claims API or Memory fallback."""

    def __init__(
        self,
        *,
        client: MnemosyneHttpClient | None = None,
        bank: MemoryBankBackend | None = None,
    ) -> None:
        self._client = client
        self._bank = bank
        self._claims_supported: bool | None = None

    def _try_native_claim(self, claim: dict[str, Any]) -> dict[str, Any] | None:
        if not self._client:
            return None
        if self._claims_supported is False:
            return None
        resp = self._client.propose_claim(
            {
                "claim": claim,
                "actor": "music_acquisition",
                "idempotency_key": f"music:claim:{claim.get('claim_id')}",
            }
        )
        if resp.get("error") == "http_404":
            self._claims_supported = False
            return None
        self._claims_supported = True
        return resp

    def persist_claim(
        self,
        claim: dict[str, Any],
        *,
        verification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        native = self._try_native_claim(claim)
        if native is not None and not native.get("error"):
            return {
                "ok": True,
                "backend": "claims_api",
                "claim": claim,
                "verification": verification,
                "response": native,
            }
        if not self._bank:
            return {
                "ok": False,
                "error": "no_bank_for_claim_fallback",
                "claim": claim,
                "note": "Node has no /api/v1/claims; configure MemoryBankBackend",
            }
        content = (
            f"[music:claim|{claim.get('epistemic')}|{claim.get('modality')}] "
            f"{claim.get('statement')}"
        )
        mem = self._bank.propose_memory(
            {
                "type": "semantic",
                "status": "candidate" if claim.get("epistemic") == "inferred" else "active",
                "content": content,
                "confidence": claim.get("confidence", 0.6),
                "idempotency_key": f"music:claim:{claim.get('claim_id')}",
                "metadata": {
                    "domain": "music",
                    "data_kind": "claim",
                    "modality": claim.get("modality"),
                    "epistemic": claim.get("epistemic"),
                    "status": claim.get("status"),
                    "claim": claim,
                    "verification": verification,
                    "target_type": (claim.get("object") or {}).get("type"),
                    "target_id": (claim.get("object") or {}).get("id"),
                    "policy": claim.get("policy") or {},
                },
            }
        )
        mid = str(mem.get("id") or (mem.get("memory") or {}).get("id") or "")
        return {
            "ok": bool(mid) or mem.get("ok") is not False,
            "backend": "memory_fallback",
            "memory_id": mid,
            "claim": claim,
            "verification": verification,
            "response": mem,
            "note": "persisted as Memory(data_kind=claim); native Claims API unavailable",
        }

    def confirm_like_claim(
        self,
        *,
        target_type: str,
        target_id: str,
        title: str = "",
        evidence_refs: list[str] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        claim = build_preference_claim(
            target_type=target_type,
            target_id=target_id,
            title=title,
            epistemic="user_confirmed",
            evidence_refs=evidence_refs,
            confidence=0.95,
            hypothesis=f"用户确认喜欢{target_type}《{title or target_id}》",
        )
        ver = build_verification_record(claim_id=claim["claim_id"], action="confirm", note=note)
        return self.persist_claim(claim, verification=ver)

    def unconfirm_like_claim(
        self,
        *,
        target_type: str,
        target_id: str,
        title: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        claim = build_preference_claim(
            target_type=target_type,
            target_id=target_id,
            title=title,
            epistemic="user_unconfirmed",
            confidence=0.9,
            status="revoked",
            hypothesis=f"用户撤销确认喜欢{target_type}《{title or target_id}》",
        )
        ver = build_verification_record(claim_id=claim["claim_id"], action="unconfirm", note=note)
        return self.persist_claim(claim, verification=ver)

    def inferred_signal_claim(
        self,
        *,
        target_type: str,
        target_id: str,
        hypothesis: str,
        evidence_refs: list[str] | None = None,
        confidence: float = 0.65,
    ) -> dict[str, Any]:
        claim = build_preference_claim(
            target_type=target_type,
            target_id=target_id,
            epistemic="inferred",
            hypothesis=hypothesis,
            evidence_refs=evidence_refs,
            confidence=confidence,
        )
        return self.persist_claim(claim)
