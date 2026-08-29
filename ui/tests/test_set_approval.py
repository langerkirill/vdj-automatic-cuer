"""Pajamathon set-file cue approval (Ready-for-Sort equivalent)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sorter import set_approval as sa


class SetApprovalStoreTests(unittest.TestCase):
    def test_approve_and_count_mismatch_needs_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sets = Path(tmp) / "Sets"
            event = sets / "Pajamathon 2026"
            event.mkdir(parents=True)
            audio = event / "014. Quimera.flac"
            audio.write_bytes(b"x")
            store = Path(tmp) / "approvals.json"
            rec = sa.approve_set_cues(
                audio,
                cue_count=6,
                loop_count=3,
                store_path=store,
                sets_root=sets,
            )
            self.assertEqual(rec["cue_count"], 6)
            self.assertTrue(
                sa.is_approved(
                    audio,
                    cue_count=6,
                    loop_count=3,
                    store_path=store,
                    sets_root=sets,
                )
            )
            self.assertFalse(
                sa.is_approved(
                    audio,
                    cue_count=7,
                    loop_count=3,
                    store_path=store,
                    sets_root=sets,
                )
            )
            self.assertTrue(
                sa.revoke_set_approval(audio, store_path=store, sets_root=sets)
            )
            self.assertFalse(
                sa.is_approved(
                    audio,
                    cue_count=6,
                    loop_count=3,
                    store_path=store,
                    sets_root=sets,
                )
            )

    def test_has_approval_ignores_cue_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sets = Path(tmp) / "Sets"
            event = sets / "Pajamathon 2026"
            event.mkdir(parents=True)
            audio = event / "001. nobody.m4a"
            audio.write_bytes(b"x")
            store = Path(tmp) / "approvals.json"
            sa.approve_set_cues(
                audio, cue_count=3, loop_count=1, store_path=store, sets_root=sets
            )
            self.assertTrue(sa.has_approval(audio, store_path=store, sets_root=sets))
            self.assertFalse(
                sa.is_approved(
                    audio, cue_count=9, loop_count=1, store_path=store, sets_root=sets
                )
            )
            self.assertIn(str(audio.resolve()), sa.approved_file_paths(store_path=store))

    def test_inbox_file_cannot_be_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "Add Cues" / "x.flac"
            inbox.parent.mkdir(parents=True)
            inbox.write_bytes(b"x")
            with self.assertRaises(ValueError):
                sa.approve_set_cues(
                    inbox,
                    cue_count=2,
                    loop_count=2,
                    store_path=Path(tmp) / "a.json",
                    sets_root=Path(tmp) / "Sets",
                )

    def test_review_status_overlay(self) -> None:
        ready = {
            "status": "ready",
            "label": "Looks ready",
            "ready": True,
        }
        review = sa.apply_set_review_status(ready, approved=False, is_cued=True)
        self.assertEqual(review["status"], "needs_review")
        self.assertEqual(review["label"], "Needs review")
        self.assertFalse(review["ready"])
        signed = sa.apply_set_review_status(ready, approved=True, is_cued=True)
        self.assertEqual(signed["status"], "approved")
        uncued = sa.apply_set_review_status(
            {"status": "not_cued", "label": "Not cued yet", "ready": False},
            approved=True,
            is_cued=False,
        )
        self.assertEqual(uncued["status"], "not_cued")


if __name__ == "__main__":
    unittest.main()
