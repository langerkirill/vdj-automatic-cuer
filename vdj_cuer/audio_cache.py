"""Per-track audio envelope cache shared by beatgrid, stems, and energy checks."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .stem_evidence import StemProfile


@dataclass
class TrackAudioCache:
    """
    Decode expensive stem/mix envelopes once per track analysis.

    Cleared after each track to keep peak RSS bounded on large libraries.
    """

    stem_profiles: Dict[str, StemProfile] = field(default_factory=dict)
    mix_profile: Optional[StemProfile] = None
    onset_envelopes: Dict[Tuple[str, Optional[str]], Tuple[list, float]] = field(
        default_factory=dict
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def get_or_load_stem_profiles(
        self, stem_files: list[tuple[str, str]]
    ) -> Dict[str, StemProfile]:
        with self._lock:
            for stem_name, path in stem_files:
                if stem_name not in self.stem_profiles:
                    self.stem_profiles[stem_name] = StemProfile.decode(path)
            return dict(self.stem_profiles)

    def get_or_load_mix_profile(self, audio_path: str) -> StemProfile:
        with self._lock:
            if self.mix_profile is None:
                self.mix_profile = StemProfile.decode(audio_path)
            return self.mix_profile

    def get_or_load_onset(
        self,
        key: Tuple[str, Optional[str]],
        loader,
    ) -> Tuple[list, float]:
        with self._lock:
            cached = self.onset_envelopes.get(key)
            if cached is not None:
                return cached
            onsets, hop = loader()
            self.onset_envelopes[key] = (onsets, hop)
            return onsets, hop

    def clear(self) -> None:
        with self._lock:
            self.stem_profiles.clear()
            self.mix_profile = None
            self.onset_envelopes.clear()
