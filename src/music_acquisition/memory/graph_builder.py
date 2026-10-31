# -*- coding: utf-8 -*-
"""Promote music evidence → candidate memories + instance relations (V2).

Rules:
- Never write confirmed likes/favorites automatically.
- saved → user_action memory + optional ``saves`` edge (observed).
- played → listened_to (observed), not likes.
- playlist membership → contains between playlist and track candidates.
- recommendation_seen → catalog only, no preference memory.
"""

from __future__ import annotations

from typing import Any

from music_acquisition.memory.mnemosyne_client import MnemosyneHttpClient

# Relations allowed for automatic observed edges (NOT likes/favorites)
_AUTO_RELATIONS = frozenset(
    {
        "contains",
        "performed_by",
        "belongs_to",
        "tagged_as",
        "listened_to",
        "saves",
        "follows",
        "imported_from",
        "supports",
    }
)


def _mem_payload(
    *,
    content: str,
    evidence: dict[str, Any],
    memory_type: str = "episodic",
    status: str = "candidate",
) -> dict[str, Any]:
    tid = str(evidence.get("target_id") or evidence.get("subject_id") or "")
    return {
        "type": memory_type,
        "status": status,
        "content": content,
        "confidence": 0.55 if evidence.get("action") == "saved" else 0.4,
        "idempotency_key": f"music:mem:{evidence.get('evidence_id') or tid}:{evidence.get('action')}",
        "metadata": {
            "domain": "music",
            "data_kind": evidence.get("data_kind") or "user_action",
            "action": evidence.get("action") or "",
            "target_type": evidence.get("target_type") or "track",
            "target_id": tid,
            "status": evidence.get("status") or "observed",
            "source_app": evidence.get("source_app") or "netease",
            "source_record_id": evidence.get("source_record_id") or tid,
            "evidence_id": evidence.get("evidence_id") or "",
            "epistemic": "observed_behavior",
            "allow_create_memory": bool((evidence.get("policy") or {}).get("allow_create_memory")),
        },
    }


def build_graph_ops_from_evidence(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure planning: which memories / relations to propose (no network)."""
    memories: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    # Track / playlist candidate ids keyed for relation wiring within this batch
    track_keys: dict[str, str] = {}  # target_id -> idempotency_key
    playlist_keys: dict[str, str] = {}

    for ev in items:
        if not isinstance(ev, dict):
            continue
        action = str(ev.get("action") or "")
        data_kind = str(ev.get("data_kind") or "")
        etype = str(ev.get("evidence_type") or "")
        tid = str(ev.get("target_id") or ev.get("subject_id") or "")
        title = str(ev.get("title") or tid or "unknown")

        if etype == "music.recommendation_seen" or action == "recommendation_seen":
            skipped.append({"reason": "recommendation_seen_catalog_only", "target_id": tid})
            continue

        if action == "saved" and (ev.get("target_type") or "track") == "track":
            content = f"[music:observed] 用户在音乐应用中收藏了歌曲《{title}》({tid})"
            mem = _mem_payload(content=content, evidence=ev)
            memories.append(mem)
            track_keys[tid] = mem["idempotency_key"]
            continue

        if action == "played":
            content = f"[music:observed] 用户播放过歌曲《{title}》({tid})"
            mem = _mem_payload(content=content, evidence=ev)
            memories.append(mem)
            track_keys[tid] = mem["idempotency_key"]
            continue

        if action in ("playlist_membership", "contains") or data_kind == "catalog":
            # playlist entity or membership note — catalog, candidate only
            if (ev.get("target_type") or "") == "playlist":
                content = f"[music:catalog] 歌单实体 {title} ({tid})"
                mem = _mem_payload(content=content, evidence={**ev, "data_kind": "catalog"})
                memories.append(mem)
                playlist_keys[tid] = mem["idempotency_key"]
            elif action == "contains":
                content = f"[music:catalog] 歌单收录歌曲《{title}》({tid})"
                mem = _mem_payload(content=content, evidence={**ev, "data_kind": "catalog"})
                memories.append(mem)
                track_keys[tid] = mem["idempotency_key"]
                pl = str((ev.get("provenance") or {}).get("playlist_id") or ev.get("playlist_id") or "")
                if pl and tid:
                    relations.append(
                        {
                            "from_key": f"playlist:{pl}",
                            "to_key": f"track:{tid}",
                            "relation": "contains",
                            "note": "playlist contains track (catalog)",
                            "metadata": {
                                "domain": "music",
                                "status": "observed",
                                "data_kind": "catalog",
                            },
                        }
                    )
            else:
                skipped.append({"reason": "catalog_no_graph", "target_id": tid, "action": action})
            continue

        skipped.append({"reason": "unhandled", "action": action, "target_id": tid})

    # After memories: planned observed edges (resolved later by submitter using returned ids)
    for tid, key in track_keys.items():
        # Find original evidence action
        for ev in items:
            if str(ev.get("target_id") or ev.get("subject_id") or "") != tid:
                continue
            action = str(ev.get("action") or "")
            if action == "saved":
                relations.append(
                    {
                        "from_key": "user:self",
                        "to_key": f"track:{tid}",
                        "relation": "saves",
                        "note": "user saved track (observed; not likes)",
                        "to_idempotency_key": key,
                        "metadata": {
                            "domain": "music",
                            "status": "observed",
                            "data_kind": "user_action",
                            "not": "likes",
                        },
                    }
                )
            elif action == "played":
                relations.append(
                    {
                        "from_key": "user:self",
                        "to_key": f"track:{tid}",
                        "relation": "listened_to",
                        "note": "user played track (observed)",
                        "to_idempotency_key": key,
                        "metadata": {
                            "domain": "music",
                            "status": "observed",
                            "data_kind": "user_action",
                        },
                    }
                )
            break

    return {
        "memories": memories,
        "relations": [r for r in relations if r.get("relation") in _AUTO_RELATIONS],
        "skipped": skipped,
        "track_keys": track_keys,
        "playlist_keys": playlist_keys,
    }


class MusicGraphBuilder:
    """Submit planned graph ops through Mnemosyne HTTP (or dry-run)."""

    def __init__(self, client: MnemosyneHttpClient | None) -> None:
        self._client = client

    def promote_evidence(
        self,
        items: list[dict[str, Any]],
        *,
        dry_run: bool = False,
        link_user_anchor: str | None = None,
    ) -> dict[str, Any]:
        plan = build_graph_ops_from_evidence(items)
        if dry_run or not self._client:
            return {
                "ok": True,
                "dry_run": True if dry_run or not self._client else False,
                "plan": plan,
                "created_memories": [],
                "created_relations": [],
            }

        created_memories: list[dict[str, Any]] = []
        id_by_key: dict[str, str] = {}
        user_anchor = (link_user_anchor or "").strip()

        for mem in plan["memories"]:
            # Guard: never auto-propose likes-labeled content as active
            meta = mem.get("metadata") or {}
            if meta.get("status") == "confirmed":
                continue
            if not meta.get("allow_create_memory") and meta.get("data_kind") == "user_action":
                # still allow candidate for saved when policy says so; else skip propose
                if meta.get("action") != "saved":
                    # played stays as observation-only unless explicitly allowed
                    if meta.get("action") == "played":
                        # propose as candidate episodic with low confidence (already set)
                        pass
            resp = self._client.propose_memory(mem)
            mid = str(resp.get("id") or (resp.get("memory") or {}).get("id") or "")
            created_memories.append({"idempotency_key": mem.get("idempotency_key"), "response": resp, "id": mid})
            tid = str(meta.get("target_id") or "")
            if mid and tid:
                id_by_key[f"track:{tid}"] = mid
                if meta.get("target_type") == "playlist":
                    id_by_key[f"playlist:{tid}"] = mid

        created_relations: list[dict[str, Any]] = []
        for rel in plan["relations"]:
            relation = str(rel.get("relation") or "")
            if relation in ("likes", "favorites"):
                created_relations.append({"skipped": True, "reason": "confirmed_only", "relation": relation})
                continue
            to_id = id_by_key.get(str(rel.get("to_key") or ""))
            from_id = user_anchor if str(rel.get("from_key")) == "user:self" else id_by_key.get(str(rel.get("from_key") or ""))
            if not from_id or not to_id:
                created_relations.append(
                    {
                        "skipped": True,
                        "reason": "missing_endpoint_ids",
                        "relation": relation,
                        "from_key": rel.get("from_key"),
                        "to_key": rel.get("to_key"),
                    }
                )
                continue
            payload = {
                "from_id": from_id,
                "to_id": to_id,
                "relation": relation,
                "note": rel.get("note") or "",
                "confidence": 0.5,
                "metadata": rel.get("metadata") or {},
            }
            resp = self._client.link_relation(payload)
            created_relations.append({"ok": resp.get("ok", True), "response": resp, "payload": payload})

        return {
            "ok": True,
            "dry_run": False,
            "plan": plan,
            "created_memories": created_memories,
            "created_relations": created_relations,
        }
