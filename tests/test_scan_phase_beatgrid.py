"""Scan Phase is VDJ's beat '1' when no beatgrid POI is present."""

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from automatic_music_cuer_gemini import AutomaticMusicCuer
from vdj_cuer.vdj_database import VdjDatabaseMixin


class ScanPhaseBeatgridTests(unittest.TestCase):
    def test_resolve_prefers_scan_phase_over_stale_poi(self):
        """Sozinho: Phase is the 1; POI one beat later must not win."""
        self.assertEqual(
            VdjDatabaseMixin._resolve_beatgrid_offset(23.562858, 22.846775),
            22.846775,
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

    def test_sozinho_cues_on_the_two_are_off_the_one(self):
        from vdj_cuer.common import is_on_downbeat, quantize_to_downbeat

        bpm = 83.99996640001345
        phase = 22.846775
        poi = 23.562858
        self.assertFalse(is_on_downbeat(poi, bpm, phase))
        snapped = quantize_to_downbeat(poi, bpm, phase)
        self.assertTrue(is_on_downbeat(snapped, bpm, phase))
        beat = 60.0 / bpm
        off = abs(snapped - phase) / beat
        self.assertLess(min(off % 4, 4 - (off % 4)), 0.08)

    def test_add_cues_keeps_this_filepath_first_one(self):
        """User-set Add Cues 1 wins over a sibling library 1."""
        from vdj_cuer.vdj_database import VdjSongMetadata

        cuer = AutomaticMusicCuer.__new__(AutomaticMusicCuer)
        add = "/Users/k/Music/DJ/Music/Cues/Add Cues/Pajamathon/01 A bout de souffle.m4a"
        lib = "/Users/k/Music/DJ/Music/Zouk/Lamba/01 A bout de souffle.m4a"
        add_meta = VdjSongMetadata(
            scan_bpm=0.697687,
            beatgrid_offset=22.423852,
            scan_phase=22.423852,
            audio_sig="WJpoZ4aHaEhXaIh3ppdqmSYH",
            file_size=9478034,
        )
        lib_meta = VdjSongMetadata(
            scan_bpm=0.697687,
            beatgrid_offset=24.440228,
            scan_phase=24.440228,
            audio_sig="WJpoZ4aHaEhXaIh3ppdqmSYH",
            file_size=9478034,
        )
        cuer._get_song_metadata = lambda path: add_meta if "Add Cues" in path else lib_meta
        cuer._get_metadata_index = lambda: {add: add_meta, lib: lib_meta}
        cuer._normalize_database_path = lambda path: path
        self.assertAlmostEqual(cuer.get_beatgrid_offset(add), 22.423852, places=5)
        self.assertAlmostEqual(cuer.get_beatgrid_offset(lib), 24.440228, places=5)


if __name__ == "__main__":
    unittest.main()
