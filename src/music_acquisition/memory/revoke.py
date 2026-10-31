# -*- coding: utf-8 -*-
"""Incremental revoke — unfavorite / removed tracks → supersede edges (no silent likes)."""

from __future__ import annotations

from typing import Any

from music_acquisition.domain.models import MusicDiff
from music_acquisition.memory.bank_backend import MemoryBankBackend


def diff_to_revoke_evidence(diff: MusicDiff) -> list[dict[str, Any]]:
    """Map removed entities to revoke evidence payloads."""
    out: list[dict[str, Any]] = []
    for ch in diff.changes:
        if ch.change_type != "removed":
            continue
        tid = str(ch.provider_entity_id or "")
        if not tid:
            continue
        out.append(
            {
                "evidence_id": f"rev_{diff.diff_id}_{tid}",
                "evidence_type": "music.favorite_removed",
                "domain": "music",
                "source_type": "external_app",
                "source_app": "netease",
                "data_kind": "user_action",
                "action": "unsaved",
                "target_type": ch.entity_type or "track",
                "target_id": tid,
                "subject_id": tid,
                "status": "observed",
                "title": "",
                "provenance": {
                    "diff_id": diff.diff_id,
                    "data_type": diff.data_type,
                    "previous_snapshot_id": diff.previous_snapshot_id,
                    "current_snapshot_id": diff.current_snapshot_id,
                    "tombstone": True,
                },
                "policy": {
                    "allow_create_memory": False,
                    "revoke_saves": True,
                    "must_not_write_likes": True,
                },
            }
        )
    return out


def apply_revokes(
    bank: MemoryBankBackend | None,
    revoke_items: list[dict[str, Any]],
    *,
    user_anchor_id: str = "",
    dry_run: bool = True,
) -> dict[str, Any]:
    """For each unsaved track: observe tombstone + supersede prior save memory if found.

    Does not delete likes (confirmed prefs need separate explicit revoke).
    """
    results: list[dict[str, Any]] = []
    if dry_run or not bank:
        return {
            "ok": True,
            "dry_run": True,
            "count": len(revoke_items),
            "items": revoke_items,
            "applied": [],
        }

    for item in revoke_items:
        tid = str(item.get("target_id") or "")
        obs = bank.observe(
            {
                "source_kind": "system",
                "content": (
                    f"[music:revoke|user_action|unsaved] target=track:{tid} "
                    f"diff={((item.get('provenance') or {}).get('diff_id'))}"
                ),
                "producer": "music_acquisition.revoke",
                "idempotency_key": f"music:revoke:{item.get('evidence_id')}",
                "metadata": {
                    **{k: item.get(k) for k in (
                        "domain", "data_kind", "action", "target_type", "target_id", "status"
                    )},
                    "music_evidence": item,
                },
            }
        )
        # Search observed save memories to supersede.
        # Graph content looks like: [music:observed] …收藏了歌曲《title》(tid)
        # Prefer target_id match; fall back to tid substring hits.
        search = bank.search(f"({tid})", limit=10, domain="music")
        if not (search.get("items") or search.get("memories")):
            search = bank.search(str(tid), limit=10, domain="music")
        items = search.get("items") or search.get("memories") or []
        superseded = []
        search_error = search.get("error")
        for mem in items:
            if not isinstance(mem, dict):
                continue
            meta = mem.get("metadata") or {}
            content = str(mem.get("content") or "")
            meta_tid = str(meta.get("target_id") or "")
            if meta_tid and meta_tid != tid:
                continue
            if not meta_tid and tid not in content:
                continue
            action = str(meta.get("action") or "")
            if action and action not in ("saved", "saves"):
                if "收藏" not in content:
                    continue
            elif not action and "收藏" not in content and "music:observed" not in content:
                continue
            mid = str(mem.get("id") or "")
            if not mid:
                continue
            upd = bank.update_memory(
                mid,
                {
                    "status": "superseded",
                    "metadata": {
                        **meta,
                        "revoked": True,
                        "revoke_evidence_id": item.get("evidence_id"),
                        "status": "observed_revoked",
                    },
                },
            )
            superseded.append({"memory_id": mid, "update": upd})
            # Confirmed likes are NOT auto-removed; only observed saves memories are superseded.
            _ = user_anchor_id  # reserved for future saves-edge unlink when relation ids are indexed
        results.append(
            {
                "target_id": tid,
                "observe": obs,
                "superseded": superseded,
                "search_error": search_error,
                "search_hits": len(items),
            }
        )
    return {"ok": True, "dry_run": False, "applied": results, "count": len(results)}
