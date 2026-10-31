# -*- coding: utf-8 -*-
"""Explicit preference confirmation → confirmed / likes (never silent).

Requires user feedback. Writes through ``MemoryBankBackend`` so Mnemosyne and
alternate banks share one contract.
"""

from __future__ import annotations

from typing import Any

from music_acquisition.memory.bank_backend import MemoryBankBackend


def _accepted(response):
    return bool(response) and response.get("ok") is not False and not response.get("error")


def confirm_preference(
    bank: MemoryBankBackend,
    *,
    user_anchor_id: str,
    target_type: str = "track",
    target_id: str,
    title: str = "",
    evidence_refs: list[str] | None = None,
    memory_id: str | None = None,
    note: str = "",
    dry_run: bool = False,
    claim_bridge: Any | None = None,
) -> dict[str, Any]:
    """Promote to confirmed preference + ``likes`` edge.

    Preconditions: caller is an explicit user action (UI/CLI/API). Acquisition
    sync paths must never call this.
    """
    tid = str(target_id or "").strip()
    anchor = str(user_anchor_id or "").strip()
    if not tid or not anchor:
        return {"ok": False, "error": "missing_target_or_user_anchor"}

    content = (
        f"[music:confirmed] 用户确认喜欢{target_type}《{title or tid}》({tid})"
        + (f" — {note}" if note else "")
    )
    mem_payload = {
        "type": "semantic",
        "status": "active",
        "content": content,
        "confidence": 0.92,
        "idempotency_key": f"music:confirmed:{target_type}:{tid}",
        "metadata": {
            "domain": "music",
            "data_kind": "preference",
            "status": "confirmed",
            "epistemic": "user_confirmed",
            "action": "likes",
            "target_type": target_type,
            "target_id": tid,
            "evidence_refs": list(evidence_refs or []),
            "policy": {"user_confirmed": True, "auto": False},
        },
    }
    plan = {
        "memory": mem_payload,
        "relation": {
            "from_id": anchor,
            "to_id": "(pending_memory_id)",
            "relation": "likes",
            "note": "user confirmed preference",
            "confidence": 0.92,
            "metadata": {
                "domain": "music",
                "status": "confirmed",
                "data_kind": "preference",
                "evidence_refs": list(evidence_refs or []),
            },
        },
        "feedback": None,
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}

    if memory_id:
        updated = bank.update_memory(
            memory_id,
            {
                "status": "active",
                "content": content,
                "confidence": 0.92,
                "metadata": mem_payload["metadata"],
            },
        )
        mid = memory_id
        mem_resp = updated
        if not _accepted(updated):
            return {"ok": False, "error": "update_failed", "response": updated}
    else:
        mem_resp = bank.propose_memory(mem_payload)
        mid = str(mem_resp.get("id") or (mem_resp.get("memory") or {}).get("id") or "")
        if not mid:
            return {"ok": False, "error": "propose_failed", "response": mem_resp}

    if mem_resp.get("status") != "active" or (mem_resp.get("metadata") or {}).get("status") != "confirmed":
        mem_resp = bank.update_memory(mid, {"status": "active", "content": content, "confidence": .92, "metadata": mem_payload["metadata"]})
        if not _accepted(mem_resp):
            return {"ok": False, "error": "update_failed", "response": mem_resp}
    listed = bank.list_relations(mid)
    if not _accepted(listed):
        return {"ok": False, "error": "relation_lookup_failed", "response": listed}
    exists = any(r.get("from_id") == anchor and r.get("to_id") == mid and r.get("relation") == "likes" for r in listed.get("items", []))
    rel_payload = {
        "from_id": anchor,
        "to_id": mid,
        "relation": "likes",
        "note": "user confirmed preference",
        "confidence": 0.92,
        "metadata": plan["relation"]["metadata"],
    }
    rel_resp = {"ok": True, "reused": True} if exists else bank.link_relation(rel_payload)
    fb_resp = {"ok": True, "skipped": "already_confirmed"} if exists else bank.feedback(
        {
            "memory_id": mid,
            "useful": True,
            "incorrect": False,
            "note": note or "music.preference.confirm",
            "producer": "music_acquisition.preference_confirm",
        }
    )
    claim_resp = None
    if claim_bridge is not None:
        try:
            claim_resp = claim_bridge.confirm_like_claim(
                target_type=target_type,
                target_id=tid,
                title=title,
                evidence_refs=evidence_refs,
                note=note,
            )
        except Exception as exc:  # noqa: BLE001
            claim_resp = {"ok": False, "error": str(exc)}
    audit = {
        "ok": _accepted(mem_resp) and _accepted(rel_resp) and _accepted(fb_resp) and (claim_resp is None or _accepted(claim_resp)),
        "dry_run": False,
        "memory_id": mid,
        "memory": mem_resp,
        "claim": claim_resp,
        "relation": rel_resp,
        "feedback": fb_resp,
        "target_id": tid,
        "target_type": target_type,
    }
    write_audit = getattr(bank, "write_audit", None)
    if callable(write_audit):
        write_audit("preference_confirm", audit)
    return audit


def reject_preference(
    bank: MemoryBankBackend,
    *,
    memory_id: str = "",
    target_type: str = "track",
    target_id: str = "",
    note: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """User rejects an inferred preference — invalidate, never write likes."""
    mid = str(memory_id or "").strip()
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "plan": {
                "action": "invalidate",
                "memory_id": mid,
                "target_id": target_id,
                "must_not_write_likes": True,
            },
        }
    if not mid and target_id:
        # best-effort: search local/mnemosyne for inferred signal
        found = bank.search(f"music:inference {target_id}", limit=5, domain="music")
        items = found.get("items") or found.get("memories") or []
        for it in items:
            if isinstance(it, dict) and str((it.get("metadata") or {}).get("target_id") or "") == str(
                target_id
            ):
                mid = str(it.get("id") or "")
                break
    if not mid:
        return {"ok": False, "error": "memory_not_found", "must_not_write_likes": True}
    updated = bank.update_memory(
        mid,
        {
            "status": "invalidated",
            "metadata": {
                "domain": "music",
                "data_kind": "preference_signal",
                "status": "rejected",
                "epistemic": "user_rejected",
                "target_type": target_type,
                "target_id": target_id,
                "policy": {"must_not_write_likes": True, "user_rejected": True},
                "reject_note": note,
            },
        },
    )
    fb = bank.feedback(
        {
            "memory_id": mid,
            "useful": False,
            "incorrect": True,
            "note": note or "music.preference.reject",
            "producer": "music_acquisition.preference_confirm",
        }
    )
    return {"ok": _accepted(updated) and _accepted(fb), "memory_id": mid, "update": updated, "feedback": fb, "likes_written": False}


def unconfirm_preference(
    bank: MemoryBankBackend,
    *,
    user_anchor_id: str = "",
    target_type: str = "track",
    target_id: str = "",
    title: str = "",
    memory_id: str = "",
    note: str = "",
    dry_run: bool = False,
    claim_bridge: Any | None = None,
) -> dict[str, Any]:
    """Explicit user revoke of a prior confirmed like.

    Separate from NetEase unfavorite (``unsaved`` / saves supersede).
    Requires explicit user action — never called from sync.
    """
    mid = str(memory_id or "").strip()
    tid = str(target_id or "").strip()
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "plan": {
                "action": "unconfirm_likes",
                "memory_id": mid,
                "target_id": tid,
                "target_type": target_type,
                "does_not_touch_saves": True,
            },
        }

    if not mid:
        # Prefer confirmed preference memory
        q = f"music:confirmed {tid}" if tid else "music:confirmed"
        found = bank.search(q, limit=10, domain="music")
        items = found.get("items") or found.get("memories") or []
        for it in items:
            if not isinstance(it, dict):
                continue
            meta = it.get("metadata") or {}
            if str(meta.get("status") or "") not in ("confirmed", "active", ""):
                if str(meta.get("data_kind") or "") != "preference":
                    continue
            if tid and str(meta.get("target_id") or "") != tid:
                continue
            if str(meta.get("action") or "") not in ("likes", "confirmed", ""):
                if "确认喜欢" not in str(it.get("content") or "") and str(meta.get("data_kind")) != "preference":
                    continue
            mid = str(it.get("id") or "")
            if not tid:
                tid = str(meta.get("target_id") or "")
            if mid:
                break
    if not mid:
        return {"ok": False, "error": "confirmed_memory_not_found", "target_id": tid}

    updated = bank.update_memory(
        mid,
        {
            "status": "invalidated",
            "content": (
                f"[music:unconfirmed] 用户撤销确认喜欢{target_type}"
                f"《{title or tid or mid}》({tid or mid})"
                + (f" — {note}" if note else "")
            ),
            "metadata": {
                "domain": "music",
                "data_kind": "preference",
                "status": "unconfirmed",
                "epistemic": "user_unconfirmed",
                "action": "unlikes",
                "target_type": target_type,
                "target_id": tid,
                "policy": {
                    "user_unconfirmed": True,
                    "auto": False,
                    "does_not_touch_saves": True,
                },
                "unconfirm_note": note,
            },
        },
    )
    # Soft-revoke likes edges (local bank deletes; Mnemosyne marks via observe + optional API)
    rev = {"ok": True, "skipped": "no_revoke_relation"}
    revoke_fn = getattr(bank, "revoke_relations", None)
    if callable(revoke_fn):
        rev = revoke_fn(
            from_id=str(user_anchor_id or ""),
            to_id=mid,
            relation="likes",
        )
    fb = bank.feedback(
        {
            "memory_id": mid,
            "useful": False,
            "incorrect": True,
            "note": note or "music.preference.unconfirm",
            "producer": "music_acquisition.preference_confirm",
        }
    )
    bank.observe(
        {
            "source_kind": "feedback",
            "content": f"[music:unconfirm|preference|unlikes] target={target_type}:{tid} mem={mid}",
            "producer": "music_acquisition.preference_confirm",
            "idempotency_key": f"music:unconfirm:{mid}:{tid}",
            "metadata": {
                "domain": "music",
                "data_kind": "preference",
                "action": "unlikes",
                "status": "unconfirmed",
                "target_id": tid,
                "memory_id": mid,
            },
        }
    )
    claim_resp = None
    if claim_bridge is not None:
        try:
            claim_resp = claim_bridge.unconfirm_like_claim(
                target_type=target_type,
                target_id=tid,
                title=title,
                note=note,
            )
        except Exception as exc:  # noqa: BLE001
            claim_resp = {"ok": False, "error": str(exc)}
    audit = {
        "ok": _accepted(updated) and _accepted(rev) and _accepted(fb) and (claim_resp is None or _accepted(claim_resp)),
        "dry_run": False,
        "memory_id": mid,
        "target_id": tid,
        "update": updated,
        "claim": claim_resp,
        "relation_revoke": rev,
        "feedback": fb,
        "saves_untouched": True,
    }
    write_audit = getattr(bank, "write_audit", None)
    if callable(write_audit):
        write_audit("preference_unconfirm", audit)
    return audit
