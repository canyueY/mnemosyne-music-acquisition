# -*- coding: utf-8 -*-
"""PreferenceSignal builder (V3) — inferred only, never auto-confirmed.

Aggregates observed user_action evidence into soft signals with evidence_refs.
Does not write likes/favorites relations.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def build_preference_signals(
    evidence_items: list[dict[str, Any]],
    *,
    min_saves_for_artist: int = 3,
    min_plays_for_track: int = 3,
) -> list[dict[str, Any]]:
    """Return PreferenceSignal payloads (status=inferred).

    Inputs are evidence dicts from MusicMemoryAdapter (or submitted metadata).
    """
    saves_by_artist: dict[str, list[str]] = defaultdict(list)
    plays_by_track: Counter[str] = Counter()
    save_evidence: dict[str, list[str]] = defaultdict(list)
    play_evidence: dict[str, list[str]] = defaultdict(list)
    titles: dict[str, str] = {}

    for ev in evidence_items:
        if not isinstance(ev, dict):
            continue
        if str(ev.get("domain") or "music") != "music":
            continue
        action = str(ev.get("action") or "")
        tid = str(ev.get("target_id") or ev.get("subject_id") or "")
        eid = str(ev.get("evidence_id") or "")
        title = str(ev.get("title") or tid)
        if tid:
            titles[tid] = title
        artists = ev.get("artists")
        artist_names: list[str] = []
        if isinstance(artists, str) and artists.strip():
            artist_names = [a.strip() for a in artists.replace("、", "/").split("/") if a.strip()]
        elif isinstance(artists, list):
            for a in artists:
                if isinstance(a, dict) and a.get("name"):
                    artist_names.append(str(a["name"]))
                elif isinstance(a, str) and a.strip():
                    artist_names.append(a.strip())

        if action == "saved" and tid:
            save_evidence[tid].append(eid)
            for name in artist_names or ["(unknown_artist)"]:
                saves_by_artist[name].append(tid)
        elif action == "played" and tid:
            plays_by_track[tid] += 1
            play_evidence[tid].append(eid)

    signals: list[dict[str, Any]] = []

    for artist, track_ids in saves_by_artist.items():
        uniq = sorted(set(track_ids))
        if len(uniq) < int(min_saves_for_artist):
            continue
        refs = []
        for tid in uniq:
            refs.extend(save_evidence.get(tid) or [])
        signals.append(
            {
                "signal_id": f"pref:artist:{artist}",
                "domain": "music",
                "data_kind": "preference_signal",
                "status": "inferred",
                "target_type": "artist",
                "target_id": artist,
                "hypothesis": f"用户可能偏好艺术家「{artist}」的作品（基于 {len(uniq)} 首收藏）",
                "confidence": min(0.85, 0.4 + 0.08 * len(uniq)),
                "evidence_refs": [r for r in refs if r][:40],
                "support_count": len(uniq),
                "policy": {
                    "not_confirmed": True,
                    "must_not_write_likes": True,
                    "requires_user_feedback_to_confirm": True,
                },
            }
        )

    for tid, count in plays_by_track.items():
        if count < int(min_plays_for_track):
            continue
        signals.append(
            {
                "signal_id": f"pref:track_repeat:{tid}",
                "domain": "music",
                "data_kind": "preference_signal",
                "status": "inferred",
                "target_type": "track",
                "target_id": tid,
                "hypothesis": (
                    f"用户可能对《{titles.get(tid, tid)}》有重复聆听倾向（播放 {count} 次）"
                ),
                "confidence": min(0.8, 0.35 + 0.1 * count),
                "evidence_refs": [r for r in (play_evidence.get(tid) or []) if r][:40],
                "support_count": count,
                "policy": {
                    "not_confirmed": True,
                    "must_not_write_likes": True,
                    "requires_user_feedback_to_confirm": True,
                },
            }
        )

    signals.sort(key=lambda s: float(s.get("confidence") or 0), reverse=True)
    return signals


def preference_signals_to_memory_payloads(
    signals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map signals → MemoryPropose payloads (always candidate + inferred)."""
    out: list[dict[str, Any]] = []
    for sig in signals:
        out.append(
            {
                "type": "semantic",
                "status": "candidate",
                "content": f"[music:inference] {sig.get('hypothesis')}",
                "confidence": float(sig.get("confidence") or 0.4),
                "idempotency_key": f"music:pref:{sig.get('signal_id')}",
                "metadata": {
                    "domain": "music",
                    "data_kind": "preference_signal",
                    "status": "inferred",
                    "epistemic": "inference",
                    "signal": sig,
                    "evidence_refs": list(sig.get("evidence_refs") or []),
                    "policy": sig.get("policy") or {},
                },
            }
        )
    return out


class PreferenceSignalPublisher:
    def __init__(self, client: Any | None) -> None:
        self._client = client

    def publish(
        self,
        evidence_items: list[dict[str, Any]],
        *,
        dry_run: bool = False,
        min_saves_for_artist: int = 3,
        min_plays_for_track: int = 3,
    ) -> dict[str, Any]:
        signals = build_preference_signals(
            evidence_items,
            min_saves_for_artist=min_saves_for_artist,
            min_plays_for_track=min_plays_for_track,
        )
        payloads = preference_signals_to_memory_payloads(signals)
        if dry_run or not self._client:
            return {
                "ok": True,
                "dry_run": True,
                "signals": signals,
                "payloads": payloads,
                "created": [],
            }
        created = []
        for body in payloads:
            # Hard guard: never send status=active for inferences
            body["status"] = "candidate"
            meta = body.setdefault("metadata", {})
            meta["status"] = "inferred"
            resp = self._client.propose_memory(body)
            created.append({"idempotency_key": body.get("idempotency_key"), "response": resp})
        return {
            "ok": True,
            "dry_run": False,
            "signals": signals,
            "created": created,
        }
