# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from music_acquisition.domain.models import MusicDiff, MusicDiffItem
from music_acquisition.memory.bank_backend import LocalFileBankBackend, open_memory_bank
from music_acquisition.memory.preference_confirm import (
    confirm_preference,
    reject_preference,
    unconfirm_preference,
)
from music_acquisition.memory.revoke import apply_revokes, diff_to_revoke_evidence
from music_acquisition.source.netease.weapi import weapi_encrypt


class ConfirmRevokeBankTests(unittest.TestCase):
    def test_local_bank_confirm_writes_likes_not_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bank = LocalFileBankBackend(Path(tmp) / "bank")
            anchor = bank.ensure_user_anchor(subject_key="u1")
            self.assertTrue(anchor.get("id"))
            plan = confirm_preference(
                bank,
                user_anchor_id=anchor["id"],
                target_id="99",
                title="Song",
                dry_run=True,
            )
            self.assertTrue(plan["ok"])
            self.assertEqual(plan["plan"]["relation"]["relation"], "likes")
            done = confirm_preference(
                bank,
                user_anchor_id=anchor["id"],
                target_id="99",
                title="Song",
                dry_run=False,
            )
            self.assertTrue(done["ok"])
            self.assertEqual(done["relation"]["relation"], "likes")
            # reject path never writes likes
            rej = reject_preference(bank, memory_id=done["memory_id"], dry_run=False)
            self.assertTrue(rej["ok"])
            self.assertEqual(rej.get("likes_written"), False)

    def test_unconfirm_like_revokes_edge_not_saves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bank = LocalFileBankBackend(Path(tmp) / "bank")
            anchor = bank.ensure_user_anchor(subject_key="u1")
            done = confirm_preference(
                bank,
                user_anchor_id=anchor["id"],
                target_id="42",
                title="Keep",
                dry_run=False,
            )
            self.assertTrue(done["ok"])
            # saves-like observed memory should remain if present — unconfirm only likes
            bank.propose_memory(
                {
                    "content": "用户在音乐应用中收藏了歌曲《Keep》(42)",
                    "status": "candidate",
                    "metadata": {"domain": "music", "action": "saved", "target_id": "42"},
                }
            )
            out = unconfirm_preference(
                bank,
                user_anchor_id=anchor["id"],
                target_id="42",
                memory_id=done["memory_id"],
                dry_run=False,
            )
            self.assertTrue(out["ok"])
            self.assertTrue(out.get("saves_untouched"))
            self.assertGreaterEqual(int(out.get("relation_revoke", {}).get("removed") or 0), 1)
            # confirmed memory invalidated
            mem = json.loads(
                (Path(tmp) / "bank" / "memories" / f"{done['memory_id']}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(mem.get("status"), "invalidated")

    def test_diff_revoke_evidence(self) -> None:
        diff = MusicDiff(
            diff_id="diff_1",
            subject_id="u",
            data_type="liked_songs",
            previous_snapshot_id="a",
            current_snapshot_id="b",
            previous_hash="h1",
            current_hash="h2",
            changes=[
                MusicDiffItem(change_type="removed", entity_type="track", provider_entity_id="7"),
                MusicDiffItem(change_type="added", entity_type="track", provider_entity_id="8"),
            ],
            generated_at="2026-10-31T00:00:00Z",
        )
        items = diff_to_revoke_evidence(diff)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["action"], "unsaved")
        self.assertTrue(items[0]["policy"]["must_not_write_likes"])
        with tempfile.TemporaryDirectory() as tmp:
            bank = open_memory_bank("local_file", local_root=Path(tmp))
            # seed a save memory
            bank.propose_memory(
                {
                    "content": "用户在音乐应用中收藏了歌曲《x》(7)",
                    "status": "candidate",
                    "metadata": {"domain": "music", "action": "saved", "target_id": "7"},
                }
            )
            applied = apply_revokes(bank, items, dry_run=False)
            self.assertTrue(applied["ok"])
            self.assertEqual(applied["count"], 1)

    def test_weapi_encrypt_shape(self) -> None:
        form = weapi_encrypt({"uid": "1", "type": "1"})
        self.assertIn("params", form)
        self.assertIn("encSecKey", form)
        self.assertEqual(len(form["encSecKey"]), 256)


if __name__ == "__main__":
    unittest.main()
