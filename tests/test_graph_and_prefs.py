# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from music_acquisition.memory.graph_builder import build_graph_ops_from_evidence
from music_acquisition.memory.preference_signals import (
    build_preference_signals,
    preference_signals_to_memory_payloads,
)


def _ev(action: str, tid: str, *, artists: str = "", title: str = "", eid: str = "") -> dict:
    return {
        "evidence_id": eid or f"ev_{tid}_{action}",
        "domain": "music",
        "data_kind": "user_action" if action in ("saved", "played") else "catalog",
        "action": action,
        "target_type": "track",
        "target_id": tid,
        "subject_id": tid,
        "title": title or tid,
        "artists": artists,
        "status": "observed",
        "evidence_type": f"music.{action}",
        "policy": {"allow_create_memory": action == "saved"},
    }


class GraphAndPrefsTests(unittest.TestCase):
    def test_graph_plan_saved_and_played_not_likes(self) -> None:
        items = [
            _ev("saved", "1", title="A"),
            _ev("played", "2", title="B"),
            _ev("recommendation_seen", "3", title="C"),
        ]
        items[2]["data_kind"] = "catalog"
        plan = build_graph_ops_from_evidence(items)
        self.assertEqual(len(plan["memories"]), 2)
        self.assertTrue(all("likes" not in (m.get("content") or "") for m in plan["memories"]))
        rels = {r["relation"] for r in plan["relations"]}
        self.assertIn("saves", rels)
        self.assertIn("listened_to", rels)
        self.assertNotIn("likes", rels)
        self.assertNotIn("favorites", rels)
        self.assertTrue(any(s.get("reason") == "recommendation_seen_catalog_only" for s in plan["skipped"]))

    def test_preference_signals_inferred_only(self) -> None:
        items = []
        for i in range(4):
            items.append(
                _ev(
                    "saved",
                    str(100 + i),
                    artists="ArtistX",
                    title=f"T{i}",
                    eid=f"e{i}",
                )
            )
        for _ in range(3):
            items.append(_ev("played", "999", title="RepeatMe", eid="p1"))
        signals = build_preference_signals(items, min_saves_for_artist=3, min_plays_for_track=3)
        self.assertTrue(any(s["target_type"] == "artist" and s["status"] == "inferred" for s in signals))
        self.assertTrue(any(s["target_id"] == "999" for s in signals))
        self.assertTrue(all(s["policy"]["must_not_write_likes"] for s in signals))
        payloads = preference_signals_to_memory_payloads(signals)
        self.assertTrue(all(p["status"] == "candidate" for p in payloads))
        self.assertTrue(all(p["metadata"]["status"] == "inferred" for p in payloads))
        self.assertTrue(all(p["metadata"]["epistemic"] == "inference" for p in payloads))


if __name__ == "__main__":
    unittest.main()
