"""VirtualDJ Sideview rec list writer."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from sorter.vdj_sideview_recs import (
    build_virtual_folder_xml,
    write_sideview_recs,
)


class VdjSideviewRecsTests(unittest.TestCase):
    def test_virtual_folder_xml_prefixes_and_escapes(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as fh:
            fh.write(b"x")
            real = fh.name
        xml = build_virtual_folder_xml(
            [
                {
                    "path": real,
                    "artist": "Rubí",
                    "title": "Seadoo",
                    "bpm": 90.0,
                    "key": "A",
                    "cue_count": 4,
                }
            ],
            prefixes=["↑"],
        )
        self.assertIn('title="↑ Seadoo"', xml)
        self.assertIn("path=", xml)
        self.assertIn('bpm="90.000"', xml)
        self.assertIn("<VirtualFolder", xml)
        missing = build_virtual_folder_xml(
            [{"path": "/no/such/file.flac", "title": "Gone", "cue_count": 3}],
            prefixes=["↑"],
        )
        self.assertNotIn("Gone", missing)

    def test_write_sideview_recs_creates_four_lists(self, tmp_path: Path | None = None):
        dest = Path(self.id().replace(".", "_") + "_mylists")
        # Use a temp dir via patch
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            mylists = Path(td) / "MyLists"
            settings = Path(td) / "settings.xml"
            settings.write_text(
                "<settings><sideviewShortcuts>/old/Bassy</sideviewShortcuts></settings>",
                encoding="utf-8",
            )
            mylists.mkdir(parents=True, exist_ok=True)
            hi = mylists / "hi.flac"
            same = mylists / "same.flac"
            lo = mylists / "lo.flac"
            for f in (hi, same, lo):
                f.write_bytes(b"x")
            result = {
                "source": {"artist": "X", "title": "Now"},
                "recommendations": {
                    "higher_energy": [
                        {
                            "path": str(hi),
                            "artist": "A",
                            "title": "High",
                            "bpm": 128,
                            "cue_count": 3,
                        }
                    ],
                    "same_energy": [
                        {
                            "path": str(same),
                            "artist": "B",
                            "title": "Hold",
                            "bpm": 126,
                            "cue_count": 3,
                        }
                    ],
                    "lower_energy": [
                        {
                            "path": str(lo),
                            "artist": "C",
                            "title": "Chill",
                            "bpm": 124,
                            "cue_count": 3,
                        }
                    ],
                },
            }
            with (
                patch("sorter.vdj_sideview_recs.VDJ_MYLISTS", mylists),
                patch("sorter.vdj_sideview_recs.VDJ_CUES_LISTS", mylists),
                patch("sorter.vdj_sideview_recs.VDJ_SETTINGS", settings),
                patch("sorter.relocate.is_virtualdj_running", return_value=False),
            ):
                out = write_sideview_recs(result)
            self.assertTrue(out["ok"])
            self.assertEqual(out["count"], 3)
            for name in ("Next Recs", "Recs Higher", "Recs Same", "Recs Lower"):
                p = mylists / f"{name}.vdjfolder"
                self.assertTrue(p.is_file(), name)
            combined = (mylists / "Next Recs.vdjfolder").read_text(encoding="utf-8")
            self.assertIn("HIGHER · High", combined)
            self.assertIn("SAME · Hold", combined)
            self.assertIn("LOWER · Chill", combined)
            settings_txt = settings.read_text(encoding="utf-8")
            self.assertIn("mylists:/Recs Higher.subfolders", settings_txt)
            self.assertIn("mylists:/Recs Same.subfolders", settings_txt)
            self.assertIn("mylists:/Recs Lower.subfolders", settings_txt)
            self.assertIn("/old/Bassy", settings_txt)
            order = (mylists / "order").read_text(encoding="utf-8")
            self.assertIn("Next Recs", order)


if __name__ == "__main__":
    unittest.main()
