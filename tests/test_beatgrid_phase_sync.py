"""Scan Phase must stay locked to beatgrid Pos for VirtualDJ UI."""

import unittest

from vdj_cuer.cue_writer import CueWriterMixin


class BeatgridPhaseSyncTests(unittest.TestCase):
    def test_updates_scan_phase_and_existing_beatgrid(self):
        song = (
            '<Song FilePath="/music/a.m4a">\r\n'
            '  <Scan Version="801" Bpm="0.5" Phase="31.425783" />\r\n'
            '  <Poi Pos="0.000000" Type="beatgrid" />\r\n'
            "</Song>"
        )
        updated = CueWriterMixin._apply_beatgrid_to_song_xml(song, 2.441838, "\r\n")
        self.assertIn('Phase="2.441838"', updated)
        self.assertIn('<Poi Pos="2.441838" Type="beatgrid" />', updated)
        self.assertNotIn('Phase="31.425783"', updated)

    def test_inserts_beatgrid_when_missing(self):
        song = (
            '<Song FilePath="/music/a.m4a">\r\n'
            '  <Scan Version="801" Bpm="0.5" Phase="0.0" />\r\n'
            '  <Poi Type="automix" Point="realStart" />\r\n'
            "</Song>"
        )
        updated = CueWriterMixin._apply_beatgrid_to_song_xml(song, 1.25, "\r\n")
        self.assertIn('Phase="1.250000"', updated)
        self.assertIn('<Poi Pos="1.250000" Type="beatgrid" />', updated)

    def test_autocue_write_does_not_rewrite_existing_grid(self):
        from vdj_cuer.cue_writer import PreparedSongCues

        song = (
            '<Song FilePath="/music/a.m4a">\r\n'
            '  <Scan Version="801" Bpm="0.75" Phase="36.096237" />\r\n'
            '  <Poi Pos="36.096237" Type="beatgrid" />\r\n'
            "</Song>"
        )
        cuer = CueWriterMixin()
        prepared = PreparedSongCues(
            cues=[],
            loops=[],
            beatgrid_offset=37.596237,
            beatgrid_corrected=True,
        )
        updated = cuer._apply_prepared_cues_to_song_xml(song, prepared)
        self.assertIn('Phase="36.096237"', updated)
        self.assertIn('<Poi Pos="36.096237" Type="beatgrid" />', updated)
        self.assertNotIn("37.596237", updated)


if __name__ == "__main__":
    unittest.main()
