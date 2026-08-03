"""Scan Phase is VDJ's beat '1' when no beatgrid POI is present."""

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from automatic_music_cuer_gemini import AutomaticMusicCuer
from vdj_cuer.vdj_database import VdjDatabaseMixin


class ScanPhaseBeatgridTests(unittest.TestCase):
    def test_resolve_prefers_beatgrid_poi_over_scan_phase(self):
        self.assertEqual(
            VdjDatabaseMixin._resolve_beatgrid_offset(0.25, 56.1),
            0.25,
        )

    def test_resolve_falls_back_to_scan_phase(self):
        self.assertEqual(
            VdjDatabaseMixin._resolve_beatgrid_offset(None, 56.103764),
            56.103764,
        )

    def test_resolve_defaults_to_zero(self):
        self.assertEqual(VdjDatabaseMixin._resolve_beatgrid_offset(None, None), 0.0)

    def test_metadata_reads_scan_phase_when_beatgrid_poi_missing(self):
        xml = """
        <Song FilePath="/music/hold me.m4a">
          <Tags Author="Y U QT" Title="Hold Me" />
          <Scan Bpm="0.434785" Phase="56.103764" />
          <Infos SongLength="199.0" />
          <Poi Name="Intro" Pos="0.0" Num="1" Type="cue" />
        </Song>
        """
        song = ET.fromstring(xml)
        reader = AutomaticMusicCuer.__new__(AutomaticMusicCuer)
        path, meta = reader._metadata_from_song(song)
        self.assertEqual(path, "/music/hold me.m4a")
        self.assertAlmostEqual(meta.beatgrid_offset, 56.103764)
        self.assertAlmostEqual(meta.scan_phase or 0.0, 56.103764)

    def test_hold_me_style_cues_snap_to_scan_phase_grid(self):
        """Cues quantized with Phase grid land on VDJ downbeats."""
        cuer = AutomaticMusicCuer.__new__(AutomaticMusicCuer)
        actual_bpm = 60.0 / 0.434785
        phase = 56.103764
        # Old wrong positions (0-phase grid)
        wrong = [0.0, 27.826240, 55.652480]
        for pos in wrong:
            fixed = cuer._quantize_grid_time(pos, actual_bpm, phase, grid_beats=4)
            # Distance to nearest Phase-grid downbeat should be ~0
            bar = (60.0 / actual_bpm) * 4.0
            steps = (fixed - phase) / bar
            self.assertAlmostEqual(steps, round(steps), places=5)


if __name__ == "__main__":
    unittest.main()
