"""House recommendation eligibility by BPM."""

from __future__ import annotations

import unittest

from sorter.recommend import HOUSE_BPM_MIN, house_eligible_for_bpm, _primary_from_picks, LibraryPick


class HouseBpmGateTests(unittest.TestCase):
    def test_house_only_above_100(self):
        self.assertFalse(house_eligible_for_bpm(None))
        self.assertFalse(house_eligible_for_bpm(62.5))
        self.assertFalse(house_eligible_for_bpm(100.0))
        self.assertFalse(house_eligible_for_bpm(HOUSE_BPM_MIN))
        self.assertTrue(house_eligible_for_bpm(100.1))
        self.assertTrue(house_eligible_for_bpm(124.0))
        self.assertTrue(house_eligible_for_bpm(128.0))

    def test_primary_prefers_higher_confidence(self):
        house = LibraryPick("Party", 0.9, "peak time")
        zouk = LibraryPick("Energy/Bouncy", 0.7, "bouncy")
        lib, pick = _primary_from_picks(
            house=house, zouk=zouk, preferred_library=None
        )
        self.assertEqual(lib, "House")
        self.assertEqual(pick.relative_path, "Party")

    def test_primary_respects_preferred_zouk(self):
        house = LibraryPick("Party", 0.95, "x")
        zouk = LibraryPick("Chill/Deep", 0.5, "y")
        lib, pick = _primary_from_picks(
            house=house, zouk=zouk, preferred_library="Zouk"
        )
        self.assertEqual(lib, "Zouk")
        self.assertEqual(pick.relative_path, "Chill/Deep")

    def test_primary_zouk_only_when_no_house(self):
        zouk = LibraryPick("Chill/Mystical", 0.8, "slow")
        lib, pick = _primary_from_picks(
            house=None, zouk=zouk, preferred_library="Both"
        )
        self.assertEqual(lib, "Zouk")
        self.assertEqual(pick.relative_path, "Chill/Mystical")


if __name__ == "__main__":
    unittest.main()
