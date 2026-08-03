import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import automatic_music_cuer_gemini as cuer_module
from vdj_audit.common import Poi, Track
from vdj_audit.inspection import grid_alignment_issues


class GridAlignmentIssueTests(unittest.TestCase):
    def test_flags_cue_off_bar_downbeat(self):
        track = Track(
            path="/music/song.m4a",
            title="Song",
            artist="A",
            length=120.0,
            pois=[
                Poi("Intro", 0.0, "cue", "4278190335", "blue"),
                Poi("Off", 0.4, "cue", "4294967040", "yellow"),  # not on bar
            ],
            beatgrid=0.0,
            scan_phase=0.0,
            scan_bpm=120.0,  # bar = 2.0s
        )
        issues = grid_alignment_issues(track)
        self.assertTrue(any("Off" in issue.cue for issue in issues))
        self.assertTrue(any("downbeat" in issue.issue.lower() or "bar" in issue.issue.lower() for issue in issues))

    def test_flags_phase_beatgrid_mismatch(self):
        track = Track(
            path="/music/song.m4a",
            title="Song",
            artist="A",
            length=120.0,
            pois=[Poi("Intro", 2.44, "cue", "4278190335", "blue")],
            beatgrid=2.44,
            scan_phase=31.4,
            scan_bpm=74.0,
        )
        issues = grid_alignment_issues(track)
        self.assertTrue(any("Phase" in issue.issue for issue in issues))

    def test_accepts_on_grid_cues(self):
        track = Track(
            path="/music/song.m4a",
            title="Song",
            artist="A",
            length=120.0,
            pois=[
                Poi("Intro", 0.0, "cue", "4278190335", "blue"),
                Poi("Drop", 8.0, "cue", "4294967040", "yellow"),  # 4 bars at 120bpm
            ],
            beatgrid=0.0,
            scan_phase=0.0,
            scan_bpm=120.0,
        )
        issues = grid_alignment_issues(track)
        self.assertEqual(issues, [])


class PostCueAuditHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_write_triggers_post_cue_audit(self):
        with patch("builtins.print"):
            cuer = cuer_module.AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path="/tmp/database.xml",
            )
        cuer.post_cue_audit_enabled = True
        cuer._validate_file_in_database = Mock(return_value=True)
        cuer._apply_cues_to_database = Mock(return_value=True)
        cuer.audit_track_after_cue = Mock(return_value={"issues": 0})
        cuer._release_track_resources = Mock()
        cuer.client = Mock()
        cuer.client.aio.files.delete = Mock()
        uploaded = Mock()
        uploaded.name = "files/one"
        cuer.upload_file_with_retry = Mock(return_value=uploaded)
        # make upload async
        async def upload(path, max_retries=5):
            return uploaded

        cuer.upload_file_with_retry = upload
        cuer.analyze_audio_with_gemini_async = Mock(
            return_value={"measure_changes": [], "loop_segments": []}
        )

        async def analyze(path, uploaded_file=None):
            return {"measure_changes": [], "loop_segments": []}

        cuer.analyze_audio_with_gemini_async = analyze

        result = await cuer.process_audio_batch_async(
            ["/music/one.flac"], dry_run=False
        )
        self.assertEqual(result, [True])
        cuer.audit_track_after_cue.assert_called_once()

    async def test_dry_run_does_not_audit(self):
        with patch("builtins.print"):
            cuer = cuer_module.AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path="/tmp/database.xml",
            )
        cuer.post_cue_audit_enabled = True
        cuer._validate_file_in_database = Mock(return_value=True)
        cuer._apply_cues_to_database = Mock(return_value=True)
        cuer.audit_track_after_cue = Mock()
        cuer._release_track_resources = Mock()

        async def upload(path, max_retries=5):
            uploaded = Mock()
            uploaded.name = "files/one"
            return uploaded

        cuer.upload_file_with_retry = upload

        async def analyze(path, uploaded_file=None):
            return {"measure_changes": [], "loop_segments": []}

        cuer.analyze_audio_with_gemini_async = analyze

        await cuer.process_audio_batch_async(["/music/one.flac"], dry_run=True)
        cuer.audit_track_after_cue.assert_not_called()


class PostCueAuditRenderTests(unittest.TestCase):
    def test_audit_writes_svg_and_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("builtins.print"):
                cuer = cuer_module.AutomaticMusicCuer(
                    gemini_api_key="test-key",
                    vdj_database_path="/tmp/database.xml",
                )
            cuer.post_cue_audit_enabled = True
            cuer.post_cue_audit_dir = temp_dir

            track = Track(
                path="/music/demo.m4a",
                title="Demo",
                artist="A",
                length=10.0,
                pois=[Poi("Intro", 0.0, "cue", "4278190335", "blue")],
                beatgrid=0.0,
                scan_phase=0.0,
                scan_bpm=120.0,
            )
            from vdj_audit.common import AudioAnalysis

            analysis = AudioAnalysis(
                duration=10.0,
                bin_seconds=10.0 / 20,
                mix=[0.1] * 20,
                stems={},
            )
            with patch(
                "vdj_cuer.post_cue_audit.load_single_track", return_value=track
            ), patch(
                "vdj_cuer.post_cue_audit.analyze_audio", return_value=analysis
            ):
                summary = cuer.audit_track_after_cue("/music/demo.m4a")

            self.assertIsNotNone(summary)
            run_dir = Path(cuer._post_cue_run_dir)
            self.assertTrue((run_dir / "index.html").exists())
            self.assertTrue(list(run_dir.glob("*.svg")))
            self.assertTrue((run_dir / "summary.tsv").exists())


if __name__ == "__main__":
    unittest.main()
