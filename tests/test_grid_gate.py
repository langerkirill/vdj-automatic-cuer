"""AutoCue must not write cues until the user 1 on disk is settled."""

from __future__ import annotations

import unittest

from vdj_cuer.grid_gate import (
    UnsettledGridError,
    assert_user_one_settled,
    user_one_is_settled,
)


class GridGateTests(unittest.TestCase):
    def test_no_preflight_is_ok(self) -> None:
        self.assertTrue(user_one_is_settled(None))

    def test_verified_grid_is_ok(self) -> None:
        self.assertTrue(
            user_one_is_settled(
                {"needs_align": False, "alignment": {"corrected": False}}
            )
        )

    def test_corrected_blocks_write(self) -> None:
        pf = {
            "needs_align": True,
            "alignment": {"corrected": True, "shift_beats": 2},
        }
        self.assertFalse(user_one_is_settled(pf))
        with self.assertRaises(UnsettledGridError):
            assert_user_one_settled(pf)

    def test_confirm_overrides_corrected(self) -> None:
        pf = {"needs_align": True, "alignment": {"corrected": True}}
        self.assertTrue(user_one_is_settled(pf, confirmed=True))
        assert_user_one_settled(pf, confirmed=True)

    def test_structural_needs_align_without_onset_allows_confirmed_path(self) -> None:
        # deep=false after "Grid is correct": Phase≠POI warn, no onset payload.
        self.assertTrue(user_one_is_settled({"needs_align": True}))


if __name__ == "__main__":
    unittest.main()
