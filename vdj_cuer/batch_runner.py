"""Compatibility wrapper for the resource-safe async batch implementation."""

from .common import *


class BatchRunnerMixin:
    def process_audio_batch(
        self, audio_file_paths: List[str], dry_run: bool = False
    ) -> List[bool]:
        """Process a batch synchronously without retaining the legacy upload path."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.process_audio_batch_async(audio_file_paths, dry_run=dry_run)
            )
        raise RuntimeError(
            "process_audio_batch() cannot run inside an event loop; "
            "await process_audio_batch_async() instead"
        )
