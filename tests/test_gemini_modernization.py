import unittest
import os
import tempfile
from unittest.mock import Mock, patch

import automatic_music_cuer_gemini as cuer_module


class GeminiModernizationTests(unittest.TestCase):
    def test_default_model_is_gemini_3_1_pro_preview(self):
        self.assertEqual(cuer_module.DEFAULT_GEMINI_MODEL, "gemini-3.1-pro-preview")

    def test_analyze_uses_existing_uploaded_file_without_reuploading(self):
        with patch("builtins.print"):
            cuer = cuer_module.AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path="/tmp/database.xml",
            )
        cuer.client = Mock()
        cuer.get_song_length = Mock(return_value=180)
        cuer.get_song_bpm_from_database = Mock(return_value=124)
        stem_uploads = [("vocal", object())]
        stem_files = [("vocal", "/tmp/vocal.m4a")]
        cuer._prepare_vdj_stems_with_retry = Mock(
            return_value=(stem_uploads, stem_files, None)
        )
        cuer._generate_music_analysis = Mock(
            return_value={
                "measure_changes": [],
                "loop_segments": [],
            }
        )

        uploaded_file = object()
        with patch("builtins.print"):
            result = cuer.analyze_audio_with_gemini("/tmp/song.mp3", uploaded_file)

        self.assertEqual(result, {"measure_changes": [], "loop_segments": []})
        cuer.client.files.upload.assert_not_called()
        cuer._prepare_vdj_stems_with_retry.assert_called_once_with("/tmp/song.mp3")
        cuer._generate_music_analysis.assert_called_once()
        self.assertEqual(cuer._generate_music_analysis.call_args.args[2], stem_uploads)
        self.assertEqual(cuer._generate_music_analysis.call_args.args[3], stem_files)

    def test_generate_json_content_uses_new_genai_client_shape(self):
        with patch("builtins.print"):
            cuer = cuer_module.AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path="/tmp/database.xml",
            )
        response = Mock()
        response.text = '{"measure_changes": [], "loop_segments": []}'
        cuer.client = Mock()
        cuer.client.models.generate_content.return_value = response

        result = cuer._generate_json_content(
            contents=["prompt", object()],
            schema=cuer_module.MusicAnalysis,
            timeout_seconds=180,
        )

        self.assertEqual(result, {"measure_changes": [], "loop_segments": []})
        call_kwargs = cuer.client.models.generate_content.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gemini-3.1-pro-preview")
        self.assertEqual(call_kwargs["config"].response_mime_type, "application/json")
        self.assertIn(
            "measure_changes",
            call_kwargs["config"].response_json_schema["properties"],
        )

    def test_detects_virtualdj_running_from_process_list(self):
        result = Mock()
        result.returncode = 0
        result.stdout = "123 /Applications/VirtualDJ.app/Contents/MacOS/VirtualDJ\n"

        with patch("subprocess.run", return_value=result):
            self.assertTrue(cuer_module.AutomaticMusicCuer.is_virtualdj_running())

    def test_detects_virtualdj_not_running(self):
        result = Mock()
        result.returncode = 1
        result.stdout = ""

        with patch("subprocess.run", return_value=result):
            self.assertFalse(cuer_module.AutomaticMusicCuer.is_virtualdj_running())

    def test_treats_rate_limit_errors_as_retryable(self):
        self.assertTrue(
            cuer_module.AutomaticMusicCuer._is_retryable_error(
                Exception("429 Resource exhausted: quota exceeded")
            )
        )

    def test_upload_uses_ascii_temp_path_for_unicode_filename(self):
        with patch("builtins.print"):
            cuer = cuer_module.AutomaticMusicCuer(
                gemini_api_key="test-key",
                vdj_database_path="/tmp/database.xml",
            )
        cuer.client = Mock()
        uploaded_file = object()
        cuer.client.files.upload.return_value = uploaded_file

        with tempfile.TemporaryDirectory() as temp_dir:
            unicode_path = os.path.join(temp_dir, "03. 2814 - 真実の恋.flac")
            with open(unicode_path, "wb") as handle:
                handle.write(b"fake audio")

            result = cuer._upload_audio_file(unicode_path)

        self.assertIs(result, uploaded_file)
        uploaded_path = cuer.client.files.upload.call_args.kwargs["file"]
        os.path.basename(uploaded_path).encode("ascii")
        self.assertTrue(uploaded_path.endswith(".flac"))


if __name__ == "__main__":
    unittest.main()
