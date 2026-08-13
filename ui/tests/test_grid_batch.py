"""BPM + bar-1 grid fixer, locked to the Pajamathon hand-edits.

Acceptance (from those real VDJ edits):
  - Halve vs keep must match what was actually needed.
  - The proposed '1' only has to land on the same beat-of-bar as the
    hand-fix. Any offset that is 0 mod 4 beats is a pass — we do not
    require the same absolute second.
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter.config import ADD_CUES
from sorter import grid_batch as gb


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pajamathon_grid_edits.json"
PAJ_DIR = ADD_CUES / "Pajamathon"


def _synthetic_onsets(
    *,
    duration: float = 24.0,
    hop: float = 0.01,
    period: float,
    phase: float = 0.0,
    extra_offbeat: float = 0.0,
) -> tuple[list[float], float]:
    """Pulse envelope: strong hits every `period` seconds (plus optional offbeats)."""
    n = int(duration / hop)
    env = [0.0] * n
    t = phase
    while t < duration:
        _paint_pulse(env, hop, t, amplitude=1.0)
        if extra_offbeat > 0:
            _paint_pulse(env, hop, t + period * 0.5, amplitude=extra_offbeat)
        t += period
    return env, hop


def _paint_pulse(env: list[float], hop: float, time_s: float, amplitude: float) -> None:
    center = int(round(time_s / hop))
    for delta in range(-4, 5):
        idx = center + delta
        if 0 <= idx < len(env):
            env[idx] = max(env[idx], amplitude * math.exp(-0.5 * (delta / 1.6) ** 2))


class DownbeatMod4Tests(unittest.TestCase):
    def test_plus_four_beats_is_the_same_one(self) -> None:
        bpm = 80.0
        period = 60.0 / bpm
        origin = 1.25
        self.assertTrue(
            gb.anchors_share_downbeat(origin, origin + 4 * period, bpm)
        )
        self.assertTrue(
            gb.anchors_share_downbeat(origin, origin + 16 * period, bpm)
        )

    def test_plus_one_beat_is_a_different_one(self) -> None:
        bpm = 90.0
        period = 60.0 / bpm
        self.assertFalse(gb.anchors_share_downbeat(2.0, 2.0 + period, bpm))
        self.assertFalse(gb.anchors_share_downbeat(2.0, 2.0 + 2 * period, bpm))

    def test_plus_eighteen_beats_equals_plus_two(self) -> None:
        """Labyrinth-style jump: +18 ≡ +2 (mod 4). Same 1 as +2, not as 0."""
        bpm = 70.0
        period = 60.0 / bpm
        origin = 39.43
        plus_18 = origin + 18 * period
        plus_2 = origin + 2 * period
        self.assertTrue(gb.anchors_share_downbeat(plus_18, plus_2, bpm))
        self.assertFalse(gb.anchors_share_downbeat(plus_18, origin, bpm))

    def test_minus_one_beat_is_phase_three(self) -> None:
        bpm = 81.0
        period = 60.0 / bpm
        origin = 6.665
        shifted = origin - period
        self.assertTrue(
            gb.anchors_share_downbeat(shifted, origin + 3 * period, bpm)
        )
        self.assertFalse(gb.anchors_share_downbeat(shifted, origin, bpm))

    def test_bar_phase_shift_wraps(self) -> None:
        self.assertEqual(gb.bar_phase_shift_beats(17.99), 2)
        self.assertEqual(gb.bar_phase_shift_beats(-1.00), 3)
        self.assertEqual(gb.bar_phase_shift_beats(22.00), 2)
        self.assertEqual(gb.bar_phase_shift_beats(-4.01), 0)
        self.assertEqual(gb.bar_phase_shift_beats(20.99), 1)
        self.assertEqual(gb.bar_phase_shift_beats(0.12), 0)


class DecideHalveTests(unittest.TestCase):
    def test_never_halve_below_110(self) -> None:
        self.assertFalse(gb.decide_halve(70.0, score_full=0.01, score_half=0.9))
        self.assertFalse(gb.decide_halve(100.0, score_full=0.02, score_half=0.4))
        self.assertFalse(gb.decide_halve(109.0, score_full=0.02, score_half=0.4))

    def test_always_halve_double_time_band(self) -> None:
        self.assertTrue(gb.decide_halve(138.0, score_full=0.2, score_half=0.2))
        self.assertTrue(gb.decide_halve(140.0, score_full=0.2, score_half=0.2))
        self.assertTrue(gb.decide_halve(143.999, score_full=0.01, score_half=0.01))
        self.assertTrue(gb.decide_halve(154.0, score_full=0.3, score_half=0.1))
        self.assertTrue(gb.decide_halve(160.0, score_full=0.2, score_half=0.2))

    def test_mid_band_needs_periodicity_or_kick_not_mix_alone(self) -> None:
        # Mix-only half-grid scores are not enough (Rejuvenate looks half-time).
        self.assertFalse(gb.decide_halve(120.0, score_full=0.10, score_half=0.18))
        self.assertTrue(
            gb.decide_halve(120.0, score_full=0.10, score_half=0.18, ac_ratio=1.32)
        )
        self.assertTrue(
            gb.decide_halve(
                120.0,
                score_full=0.06,
                score_half=0.07,
                kick_ratio=1.28,
                kick_half=0.09,
            )
        )
        self.assertTrue(
            gb.decide_halve(126.0, score_full=0.05, score_half=0.05, ac_ratio=1.60)
        )
        self.assertTrue(
            gb.decide_halve(124.0, score_full=0.05, score_half=0.06, ac_ratio=1.16)
        )
        self.assertTrue(
            gb.decide_halve(130.0, score_full=0.04, score_half=0.05, ac_ratio=1.07)
        )

    def test_ratio_without_energy_does_not_halve(self) -> None:
        self.assertFalse(
            gb.decide_halve(120.0, score_full=0.0, score_half=0.0, ac_ratio=1.5)
        )
        self.assertFalse(
            gb.decide_halve(
                120.0,
                score_full=0.0,
                score_half=0.0,
                kick_ratio=80.0,
                kick_half=0.0,
            )
        )

    def test_house_does_not_always_halve_one_fifty(self) -> None:
        self.assertFalse(
            gb.decide_halve(
                150.0, score_full=0.2, score_half=0.2, always_halve_band=False
            )
        )
        self.assertFalse(
            gb._always_halve_double_time_band("/Music/DJ/Music/House/Club/x.flac")
        )
        self.assertTrue(
            gb._always_halve_double_time_band(
                "/Music/DJ/Music/Cues/Add Cues/Pajamathon/x.flac"
            )
        )

    def test_mid_band_keeps_true_one_twenty(self) -> None:
        self.assertFalse(gb.decide_halve(120.0, score_full=0.20, score_half=0.11))
        self.assertFalse(
            gb.decide_halve(130.0, score_full=0.04, score_half=0.06, ac_ratio=0.84)
        )
        self.assertFalse(
            gb.decide_halve(135.0, score_full=0.18, score_half=0.10, ac_ratio=1.19)
        )


class FixtureContractTests(unittest.TestCase):
    """The hand-edits are the spec — unique song → expected BPM + bar phase."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = gb.load_fixture_cases(FIXTURE)

    def test_fixture_loads_unique_songs(self) -> None:
        self.assertGreaterEqual(len(self.cases), 20)
        names = [c.name for c in self.cases]
        self.assertEqual(len(names), len(set(names)))

    def test_fixture_halve_labels_match_after_bpm(self) -> None:
        halves = {c.name for c in self.cases if c.expected_halve}
        keeps = {c.name for c in self.cases if not c.expected_halve}
        self.assertTrue(any("Kweller" in n for n in halves))
        self.assertTrue(any("Kiss Me" in n for n in halves))
        self.assertTrue(any("Cold War" in n for n in halves))
        self.assertTrue(any("Come Thru" in n for n in halves))
        self.assertTrue(any("1 em 100" in n for n in halves))
        self.assertTrue(any("Calm" in n for n in halves))
        self.assertTrue(any("ocean eyes" in n for n in halves))
        self.assertTrue(any("Talk REMIX" in n for n in halves))
        self.assertTrue(any("No One" in n for n in halves))
        self.assertTrue(any("Heartless" in n for n in halves))
        self.assertTrue(any("Love In Stereo" in n for n in keeps))
        self.assertTrue(any("Rejuvenate" in n for n in keeps))
        self.assertTrue(any("Never Too Far" in n for n in keeps))
        self.assertTrue(any("Light.flac" in n for n in keeps))
        self.assertTrue(any("iNFinitY - HER" in n for n in keeps))
        self.assertTrue(any("Sola" in n for n in keeps))
        souje = next(c for c in self.cases if "Souje" in c.name)
        self.assertEqual(souje.expected_phase, 0)
        for case in self.cases:
            if case.after_bpm < 55:
                continue
            if case.expected_halve:
                self.assertAlmostEqual(
                    case.after_bpm * 2.0, case.before_bpm, delta=1.0
                )
            else:
                self.assertAlmostEqual(case.after_bpm, case.before_bpm, delta=0.5)

    def test_fixture_includes_infinity_her(self) -> None:
        her = next(c for c in self.cases if c.name == "iNFinitY - HER.mp3")
        self.assertFalse(her.expected_halve)
        self.assertEqual(her.expected_phase, 2)
        self.assertAlmostEqual(her.before_bpm, 68.0, delta=0.1)
        self.assertAlmostEqual(her.after_anchor - her.before_anchor, 1.79, delta=0.05)

    def test_propose_anchor_matches_user_phase_mod4(self) -> None:
        """Knowing which bar-beat is the 1 is enough — exact second is not required.

        Sub-beat nudges (Mágico / Chantaje / Sleep) are fine-align, not bar phase.
        """
        checked = 0
        for case in self.cases:
            frac = abs(case.delta_beats - round(case.delta_beats))
            if frac > 0.25:
                continue
            with self.subTest(case.name):
                scores = {0: 0.02, 1: 0.02, 2: 0.02, 3: 0.02}
                scores[case.expected_phase] = 0.20
                proposed = gb.propose_downbeat_anchor(
                    case.before_anchor, case.after_bpm, scores
                )
                self.assertTrue(
                    gb.anchors_share_downbeat(
                        proposed, case.after_anchor, case.after_bpm
                    ),
                    msg=(
                        f"{case.name}: proposed {proposed:.3f}s "
                        f"vs user {case.after_anchor:.3f}s "
                        f"(phase {case.expected_phase})"
                    ),
                )
                checked += 1
        self.assertGreaterEqual(checked, 12)


class SyntheticAudioPlanTests(unittest.TestCase):
    def test_double_time_one_twenty_is_halved_and_phase_kept(self) -> None:
        # True 60 BPM kicks; VDJ stored 120 with the same time origin.
        onsets, hop = _synthetic_onsets(period=1.0, phase=0.2)
        plan = gb.plan_grid_bpm_fix(
            bpm=120.0, anchor=0.2, onsets=onsets, hop_seconds=hop, name="fake-60"
        )
        self.assertTrue(plan.halve)
        self.assertAlmostEqual(plan.bpm_after, 60.0, places=3)
        self.assertTrue(gb.anchors_share_downbeat(plan.anchor_after, 0.2, 60.0))

    def test_true_one_twenty_is_kept(self) -> None:
        onsets, hop = _synthetic_onsets(period=0.5, phase=0.05, extra_offbeat=0.85)
        plan = gb.plan_grid_bpm_fix(
            bpm=120.0, anchor=0.05, onsets=onsets, hop_seconds=hop, name="fake-120"
        )
        self.assertFalse(plan.halve)
        self.assertAlmostEqual(plan.bpm_after, 120.0, places=3)

    def test_one_beat_late_grid_snaps_to_the_one(self) -> None:
        # Only the musical 1s have energy. VDJ put the 1 one beat late (70 BPM).
        beat = 60.0 / 70.0
        onsets, hop = _synthetic_onsets(period=4 * beat, phase=0.0)
        late = beat
        plan = gb.plan_grid_bpm_fix(
            bpm=70.0, anchor=late, onsets=onsets, hop_seconds=hop, name="late-1"
        )
        self.assertFalse(plan.halve)
        self.assertTrue(gb.anchors_share_downbeat(plan.anchor_after, 0.0, 70.0))
        self.assertEqual(plan.shift_beats % 4, 3)

    def test_walks_back_to_an_early_bar_with_the_same_one(self) -> None:
        beat = 60.0 / 60.0
        onsets, hop = _synthetic_onsets(period=4 * beat, phase=4.0)
        plan = gb.plan_grid_bpm_fix(
            bpm=60.0, anchor=20.0, onsets=onsets, hop_seconds=hop, name="late-bar"
        )
        self.assertFalse(plan.halve)
        self.assertTrue(gb.anchors_share_downbeat(plan.anchor_after, 4.0, 60.0))
        self.assertLess(plan.anchor_after, 12.0)

    def test_always_halve_one_fifty_four_even_if_scores_tie(self) -> None:
        onsets, hop = _synthetic_onsets(period=60.0 / 154.0, phase=0.1)
        plan = gb.plan_grid_bpm_fix(
            bpm=154.0, anchor=3.15, onsets=onsets, hop_seconds=hop, name="cold"
        )
        self.assertTrue(plan.halve)
        self.assertAlmostEqual(plan.bpm_after, 77.0, places=3)


class ApplyPlanTests(unittest.TestCase):
    def test_skip_plan_does_not_write(self) -> None:
        plan = gb.GridFixPlan(
            path="/tmp/x.flac",
            name="x.flac",
            bpm_before=90.0,
            bpm_after=90.0,
            halve=False,
            anchor_before=1.0,
            anchor_after=1.0,
            shift_beats=0,
            confidence=1.0,
            action="skip",
            reason="already on 1",
        )
        with patch.object(gb, "halve_track_bpm") as mock_bpm, patch.object(
            gb, "set_beatgrid_anchor"
        ) as mock_grid:
            result = gb.apply_grid_fix_plan(plan, dry_run=False)
        mock_bpm.assert_not_called()
        mock_grid.assert_not_called()
        self.assertEqual(result["action"], "skip")

    def test_halve_and_align_calls_both_writers(self) -> None:
        plan = gb.GridFixPlan(
            path="/tmp/x.flac",
            name="x.flac",
            bpm_before=120.0,
            bpm_after=60.0,
            halve=True,
            anchor_before=1.0,
            anchor_after=3.0,
            shift_beats=2,
            confidence=2.0,
            action="halve_and_align",
            reason="test",
        )
        with patch.object(
            gb, "halve_track_bpm", return_value={"ok": True, "bpm_after": 60.0}
        ) as mock_bpm, patch.object(
            gb, "set_beatgrid_anchor", return_value={"ok": True, "anchor": 3.0}
        ) as mock_grid, patch.object(
            gb, "is_virtualdj_running", return_value=False
        ):
            result = gb.apply_grid_fix_plan(
                plan, dry_run=False, create_backup=False
            )
        mock_bpm.assert_called_once()
        mock_grid.assert_called_once()
        self.assertEqual(result["action"], "halve_and_align")

    def test_skips_half_when_live_bpm_already_changed(self) -> None:
        plan = gb.GridFixPlan(
            path="/tmp/x.flac",
            name="x.flac",
            bpm_before=154.0,
            bpm_after=77.0,
            halve=True,
            anchor_before=1.0,
            anchor_after=1.0,
            shift_beats=0,
            confidence=2.0,
            action="halve",
            reason="test",
        )
        live = type("Cues", (), {"bpm": 77.0})()
        with patch.object(gb, "is_virtualdj_running", return_value=False), patch.object(
            gb, "summarize_cues", return_value=live
        ), patch.object(gb, "halve_track_bpm") as mock_bpm:
            result = gb.apply_grid_fix_plan(plan, dry_run=False)
        mock_bpm.assert_not_called()
        self.assertEqual(result["action"], "skipped_stale")

    def test_refuses_when_vdj_running(self) -> None:
        plan = gb.GridFixPlan(
            path="/tmp/x.flac",
            name="x.flac",
            bpm_before=120.0,
            bpm_after=60.0,
            halve=True,
            anchor_before=1.0,
            anchor_after=1.0,
            shift_beats=0,
            confidence=2.0,
            action="halve",
            reason="test",
        )
        with patch.object(gb, "is_virtualdj_running", return_value=True):
            with self.assertRaises(RuntimeError):
                gb.apply_grid_fix_plan(plan, dry_run=False)


class BatchJobTests(unittest.TestCase):
    def test_start_batch_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            gb.start_batch_grid_fix([])

    def test_rejects_second_apply_batch(self) -> None:
        fake = gb.GridFixBatch(
            id="already",
            status="running",
            apply=True,
            created_at="t",
        )
        with patch.object(gb, "is_virtualdj_running", return_value=False):
            with gb._lock:
                gb._batches["already"] = fake
            try:
                with self.assertRaises(RuntimeError) as ctx:
                    gb.start_batch_grid_fix(["/tmp/song.flac"], apply=True)
                self.assertIn("already running", str(ctx.exception).lower())
            finally:
                with gb._lock:
                    gb._batches.pop("already", None)

    def test_plan_only_batch_does_not_need_vdj_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "song.flac"
            audio.write_bytes(b"x")
            fake_plan = gb.GridFixPlan(
                path=str(audio),
                name=audio.name,
                bpm_before=90.0,
                bpm_after=90.0,
                halve=False,
                anchor_before=0.5,
                anchor_after=0.5,
                shift_beats=0,
                confidence=1.0,
                action="skip",
                reason="ok",
            )
            with patch.object(gb, "ADD_CUES", Path(tmp)), patch.object(
                gb, "LIBRARIES", {}
            ), patch.object(
                gb, "summarize_cues"
            ) as mock_cues, patch.object(
                gb, "plan_grid_bpm_fix", return_value=fake_plan
            ), patch.object(
                gb, "is_virtualdj_running", return_value=True
            ):
                mock_cues.return_value.bpm = 90.0
                mock_cues.return_value.beatgrid_pos = 0.5
                mock_cues.return_value.scan_phase = 0.5
                mock_cues.return_value.in_database = True
                mock_cues.return_value.has_beatgrid = True
                batch = gb.start_batch_grid_fix(
                    [str(audio)], apply=False, wait=True
                )
            self.assertEqual(batch.status, "ok")
            self.assertEqual(batch.skipped, 1)
            self.assertEqual(batch.failed, 0)


class LivePajamathonFixtureTests(unittest.TestCase):
    """Real files + the user's before-state. Skip a row if the audio is gone."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = [
            c
            for c in gb.load_fixture_cases(FIXTURE)
            if gb.resolve_pajamathon_audio(c.name, paj_dir=PAJ_DIR) is not None
        ]

    def test_real_songs_match_user_halve_vs_keep(self) -> None:
        if not self.cases:
            self.skipTest("Pajamathon Add Cues audio not on disk")
        failures: list[str] = []
        for case in self.cases:
            path = gb.resolve_pajamathon_audio(case.name, paj_dir=PAJ_DIR)
            if path is None:
                continue
            try:
                plan = gb.plan_grid_bpm_fix(
                    path,
                    bpm=case.before_bpm,
                    anchor=case.before_anchor,
                    name=case.name,
                )
            except Exception as exc:  # pragma: no cover - diagnostic
                failures.append(f"{case.name}: analyze failed ({exc})")
                continue
            if case.after_bpm < 55:
                continue
            if plan.halve != case.expected_halve:
                failures.append(
                    f"{case.name}: halve={plan.halve} expected "
                    f"{case.expected_halve} ({plan.reason})"
                )
        self.assertFalse(failures, "\n".join(failures))

    def test_real_songs_clear_downbeat_is_on_the_one(self) -> None:
        """When kick/mix clearly hear the 1, it must match the hand-fix (mod 4).

        Ambiguous kizomba ±1 cases stay on the current phase rather than guess.
        """
        if not self.cases:
            self.skipTest("Pajamathon Add Cues audio not on disk")
        must_match = (
            "Kweller",
            "Labyrinth",
            "Creepin",
            "Cold War",
            "Kiss Me",
            "Come Thru",
            "1 em 100",
            "Calm",
            "iNFinitY - HER",
            "ocean eyes",
            "Heartless",
            "Crowded Room",
        )
        failures: list[str] = []
        checked = 0
        for case in self.cases:
            if not any(token in case.name for token in must_match):
                continue
            path = gb.resolve_pajamathon_audio(case.name, paj_dir=PAJ_DIR)
            if path is None:
                continue
            plan = gb.plan_grid_bpm_fix(
                path,
                bpm=case.before_bpm,
                anchor=case.before_anchor,
                name=case.name,
            )
            checked += 1
            if not gb.anchors_share_downbeat(
                plan.anchor_after, case.after_anchor, plan.bpm_after
            ):
                failures.append(
                    f"{case.name}: proposed {plan.anchor_after:.3f}s "
                    f"vs user {case.after_anchor:.3f}s "
                    f"shift={plan.shift_beats} {plan.reason}"
                )
        self.assertGreaterEqual(checked, 6)
        self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
