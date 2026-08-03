import argparse
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import automatic_music_cuer_gemini as cuer_module


def write_database(path: Path, song_length: float = 123.0) -> None:
    path.write_text(
        "".join(
            [
                "<VirtualDJ_Database>",
                '<Song FilePath="/music/one.flac">',
                '<Scan Bpm="0.5" />',
                f'<Infos SongLength="{song_length}" />',
                '<Poi Type="beatgrid" Pos="0.25" Num="0" />',
                "</Song>",
                '<Song FilePath="/music/two.flac">',
                '<Tags Bpm="0.4" />',
                '<Infos SongLength="234" />',
                "</Song>",
                "</VirtualDJ_Database>",
            ]
        ),
        encoding="utf-8",
    )


class DatabaseResourceSafetyTests(unittest.TestCase):
    def make_cuer(self, database_path: str):
        with patch("builtins.print"):
            return cuer_module.AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path=database_path,
            )

    def test_read_only_metadata_uses_one_cached_streaming_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "database.xml"
            write_database(database)
            cuer = self.make_cuer(str(database))

            import xml.etree.ElementTree as element_tree

            real_iterparse = element_tree.iterparse
            with patch.object(
                element_tree, "iterparse", wraps=real_iterparse
            ) as iterparse:
                self.assertTrue(cuer._validate_file_in_database("/music/one.flac"))
                self.assertEqual(cuer.get_song_bpm_from_database("/music/one.flac"), 120)
                self.assertEqual(cuer.get_song_length("/music/one.flac"), 123)
                self.assertEqual(cuer.get_beatgrid_offset("/music/one.flac"), 0.25)

            self.assertEqual(iterparse.call_count, 1)

    def test_metadata_index_invalidates_when_database_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "database.xml"
            write_database(database, song_length=123)
            cuer = self.make_cuer(str(database))

            self.assertEqual(cuer.get_song_length("/music/one.flac"), 123)
            write_database(database, song_length=321)
            stat = database.stat()
            os.utime(database, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

            self.assertEqual(cuer.get_song_length("/music/one.flac"), 321)

    def test_valid_database_fast_path_skips_regex_preprocessing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "database.xml"
            write_database(database)
            cuer = self.make_cuer(str(database))

            with patch.object(cuer, "preprocess_xml_for_parsing") as preprocess:
                root = cuer.parse_vdj_database()

            self.assertEqual(len(root.findall("Song")), 2)
            preprocess.assert_not_called()


class BatchResourceSafetyTests(unittest.IsolatedAsyncioTestCase):
    def make_cuer(self):
        with patch("builtins.print"):
            cuer = cuer_module.AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path="/tmp/database.xml",
            )
        cuer._validate_file_in_database = Mock(return_value=True)
        cuer._apply_cues_to_database = Mock(return_value=True)
        cuer.client = Mock()
        cuer.client.aio.files.delete = AsyncMock()
        return cuer

    async def test_async_batch_uses_native_async_analysis_and_cleans_remote_files(self):
        cuer = self.make_cuer()
        uploaded = Mock(name="uploaded")
        uploaded.name = "files/one"
        cuer.upload_file_with_retry = AsyncMock(return_value=uploaded)
        cuer.analyze_audio_with_gemini = Mock(
            side_effect=AssertionError("blocking analysis path must not run")
        )
        cuer.analyze_audio_with_gemini_async = AsyncMock(
            return_value={"measure_changes": [], "loop_segments": []}
        )

        result = await cuer.process_audio_batch_async(["/music/one.flac"], dry_run=True)

        self.assertEqual(result, [True])
        cuer.analyze_audio_with_gemini_async.assert_awaited_once_with(
            "/music/one.flac", uploaded
        )
        cuer.client.aio.files.delete.assert_awaited_once_with(name="files/one")

    async def test_failed_upload_does_not_shift_results_to_another_input(self):
        cuer = self.make_cuer()
        first = Mock()
        first.name = "files/one"
        third = Mock()
        third.name = "files/three"
        cuer.upload_file_with_retry = AsyncMock(side_effect=[first, None, third])
        cuer.analyze_audio_with_gemini_async = AsyncMock(
            return_value={"measure_changes": [], "loop_segments": []}
        )

        result = await cuer.process_audio_batch_async(
            ["/music/one.flac", "/music/two.flac", "/music/three.flac"],
            dry_run=True,
        )

        self.assertEqual(result, [True, False, True])

    async def test_native_async_generation_is_cancellable(self):
        cuer = self.make_cuer()
        started = asyncio.Event()

        async def never_finishes(**kwargs):
            started.set()
            await asyncio.Event().wait()

        cuer.client.aio.models.generate_content = never_finishes
        task = asyncio.create_task(
            cuer._generate_json_content_async(
                contents=["prompt"],
                schema=cuer_module.MusicAnalysis,
                timeout_seconds=180,
            )
        )
        await started.wait()
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.2)


class CliResourceSafetyTests(unittest.TestCase):
    def test_batch_size_must_stay_within_safe_concurrency_limit(self):
        self.assertEqual(cuer_module.parse_batch_size("1"), 1)
        self.assertEqual(cuer_module.parse_batch_size("2"), 2)
        self.assertEqual(cuer_module.DEFAULT_BATCH_SIZE, 1)
        for value in ("0", "-1", "3", "100"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    cuer_module.parse_batch_size(value)


class SurgicalBatchWriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_batch_uses_surgical_apply_path(self):
        with patch("builtins.print"):
            cuer = cuer_module.AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path="/tmp/database.xml",
            )
        cuer._validate_file_in_database = Mock(return_value=True)
        cuer._apply_cues_to_database = Mock(return_value=True)
        cuer._release_track_resources = Mock()
        cuer.client = Mock()
        cuer.client.aio.files.delete = AsyncMock()
        uploaded = Mock()
        uploaded.name = "files/one"
        cuer.upload_file_with_retry = AsyncMock(return_value=uploaded)
        cuer.analyze_audio_with_gemini_async = AsyncMock(
            return_value={"measure_changes": [], "loop_segments": []}
        )
        cuer.parse_vdj_database = Mock(
            side_effect=AssertionError("full DOM batch path must not run")
        )

        result = await cuer.process_audio_batch_async(
            ["/music/one.flac"], dry_run=False
        )

        self.assertEqual(result, [True])
        cuer._apply_cues_to_database.assert_called_once()
        cuer._release_track_resources.assert_called()


if __name__ == "__main__":
    unittest.main()
