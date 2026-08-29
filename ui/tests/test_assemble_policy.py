"""Assemble mix/rank policy lives in sorter.assemble_policy — not playlist I/O."""

from __future__ import annotations

import unittest

from sorter import assemble_policy
from sorter import playlist_assemble as assemble_mod


class AssemblePolicyHomeTests(unittest.TestCase):
    def test_crate_lane_from_top_folder(self) -> None:
        self.assertEqual(
            assemble_policy.crate_lane({"relative_path": "Chill/Mystical/x.flac"}),
            "chill",
        )
        self.assertEqual(
            assemble_policy.crate_lane({"relative_path": "Jr&B/slow.flac"}),
            "rnb",
        )

    def test_rank_score_weights_fit_and_recency(self) -> None:
        now = 1_700_000_000.0
        fresh = assemble_policy.rank_score(
            {"fit": 1.0, "first_seen": now - 86400}, now=now
        )
        old = assemble_policy.rank_score(
            {"fit": 1.0, "first_seen": now - 200 * 86400}, now=now
        )
        self.assertGreater(fresh, old)
        self.assertAlmostEqual(fresh, 0.72 * 1.0 + 0.28 * 1.0, places=4)

    def test_normalize_shares_and_job_busy(self) -> None:
        shares = assemble_policy.normalize_lane_shares({"chill": 80, "energy": 20})
        self.assertAlmostEqual(shares["chill"], 0.8, places=5)
        self.assertTrue(
            assemble_policy.assemble_job_busy({"id": "j", "status": "running"})
        )
        self.assertFalse(assemble_policy.assemble_job_busy({"id": "j", "status": "ok"}))

    def test_playlist_assemble_reexports_the_same_functions(self) -> None:
        self.assertIs(assemble_mod.crate_lane, assemble_policy.crate_lane)
        self.assertIs(assemble_mod.rank_score, assemble_policy.rank_score)
        self.assertIs(assemble_mod.normalize_lane_shares, assemble_policy.normalize_lane_shares)
        self.assertIs(assemble_mod.clamp_target, assemble_policy.clamp_target)


if __name__ == "__main__":
    unittest.main()
