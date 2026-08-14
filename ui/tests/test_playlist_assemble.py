"""Playlist assembly: newest-first chunks and Pajamathon ranking."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from sorter.playlist_assemble import (
    DEFAULT_EVENT,
    DEFAULT_MIN_FIT,
    KEEP_FIT,
    LANE_SHARE,
    assemble_playlist,
    crate_lane,
    chunk_tracks,
    interleave_by_lane,
    clamp_target,
    clip_start_sec,
    clone_cues_for_set_paths,
    stage_uncued_playlist_tracks,
    dedupe_tracks_for_eval,
    event_folder_name,
    extract_listen_clip,
    load_mix_prefs,
    load_score_cache,
    preview_library,
    result_from_cache,
    normalize_lane_shares,
    normalize_min_fit,
    rebalance_latest_playlist,
    resolve_mix,
    materialize_set_directory,
    recency_score,
    rank_score,
    save_mix_prefs,
    save_score_cache,
    slug_event,
    start_assemble_job,
    tracks_from_score_cache,
    persist_job,
    latest_job,
    load_job_snapshot,
    job_from_dict,
    AssembleJob,
    _cache_covers_track,
    _merge_scored,
    _track_idents,
    list_library_tracks,
    path_is_assemble_excluded,
    track_is_assemble_excluded,
)

# Never write mix prefs / job snapshots into ~/Music/DJ/Notes during the suite.
_MIX_TMP: tempfile.TemporaryDirectory | None = None
_MIX_PATCH: object | None = None
_JOB_PATCH: object | None = None


def setUpModule() -> None:
    global _MIX_TMP, _MIX_PATCH, _JOB_PATCH
    _MIX_TMP = tempfile.TemporaryDirectory()
    _MIX_PATCH = patch(
        "sorter.playlist_assemble.MIX_PREFS_PATH",
        Path(_MIX_TMP.name) / "playlist_assemble_mix.json",
    )
    _JOB_PATCH = patch(
        "sorter.playlist_assemble.JOB_SNAPSHOT_PATH",
        Path(_MIX_TMP.name) / "playlist_assemble_job.json",
    )
    _MIX_PATCH.start()
    _JOB_PATCH.start()


def tearDownModule() -> None:
    global _MIX_TMP, _MIX_PATCH, _JOB_PATCH
    if _JOB_PATCH is not None:
        _JOB_PATCH.stop()
        _JOB_PATCH = None
    if _MIX_PATCH is not None:
        _MIX_PATCH.stop()
        _MIX_PATCH = None
    if _MIX_TMP is not None:
        _MIX_TMP.cleanup()
        _MIX_TMP = None


def _track(
    path: str,
    *,
    first_seen: float,
    fit: float,
    verdict: str = "keep",
    artist: str = "A",
    title: str = "T",
    vibe: str = "",
    relative_path: str = "",
) -> dict:
    return {
        "path": path,
        "name": Path(path).name,
        "artist": artist,
        "title": title,
        "vibe": vibe,
        "relative_path": relative_path or Path(path).name,
        "first_seen": first_seen,
        "mtime": first_seen,
        "fit": fit,
        "verdict": verdict,
        "reason": "test",
    }


class PlaylistAssembleHelpersTests(unittest.TestCase):
    def test_clamp_target_300_to_500(self):
        self.assertEqual(clamp_target(50), 300)
        self.assertEqual(clamp_target(400), 400)
        self.assertEqual(clamp_target(900), 500)

    def test_chunk_tracks_size(self):
        tracks = [{"path": str(i)} for i in range(37)]
        chunks = chunk_tracks(tracks, 16)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(len(chunks[0]), 16)
        self.assertEqual(len(chunks[-1]), 5)

    def test_recency_prefers_new(self):
        now = time.time()
        self.assertGreater(recency_score(now - 3 * 86400), recency_score(now - 200 * 86400))

    def test_assemble_throws_in_newest_and_caps_target(self):
        now = time.time()
        scored = []
        # 20 brand-new mid-fit tracks
        for i in range(20):
            scored.append(
                _track(
                    f"/zouk/new{i}.flac",
                    first_seen=now - i * 3600,
                    fit=0.65,
                    title=f"New {i}",
                )
            )
        # 400 older high-fit tracks
        for i in range(400):
            scored.append(
                _track(
                    f"/zouk/old{i}.flac",
                    first_seen=now - 400 * 86400,
                    fit=0.92,
                    title=f"Old {i}",
                )
            )
        playlist = assemble_playlist(scored, target=350, newest_guarantee=30)
        self.assertGreaterEqual(len(playlist), 300)
        self.assertLessEqual(len(playlist), 350)
        new_count = sum(1 for p in playlist if p["title"].startswith("New "))
        self.assertGreaterEqual(new_count, 20)

    def test_assemble_playlist_drops_folder_twins(self):
        now = time.time()
        scored = [
            _track(
                "/Zouk/Energy/Trappy/Peekaboo - Here With Me.flac",
                first_seen=now,
                fit=0.88,
                artist="Peekaboo",
                title="Here With Me",
                vibe="Energy / Trappy",
                relative_path="Energy/Trappy/Peekaboo - Here With Me.flac",
            ),
            _track(
                "/Zouk/Energy/Epic/Peekaboo - Here With Me.flac",
                first_seen=now - 1,
                fit=0.88,
                artist="Peekaboo",
                title="Here With Me",
                vibe="Energy / Epic",
                relative_path="Energy/Epic/Peekaboo - Here With Me.flac",
            ),
            _track(
                "/Zouk/Chill/Mystical/Roderic, Jacqueline Jones - Passengers (Original Mix).flac",
                first_seen=now - 2,
                fit=0.92,
                artist="Roderic, Jacqueline Jones",
                title="Passengers (Original Mix)",
                vibe="Chill / Mystical",
                relative_path="Chill/Mystical/x.flac",
            ),
            _track(
                "/Zouk/Chill/Journey/Roderic, Jacqueline Jones - Passengers (Original Mix).flac",
                first_seen=now - 3,
                fit=0.92,
                artist="Roderic, Jacqueline Jones",
                title="Passengers (Original Mix)",
                vibe="Chill / Journey",
                relative_path="Chill/Journey/x.flac",
            ),
        ]
        playlist = assemble_playlist(scored, target=300, newest_guarantee=10)
        titles = [(p["artist"], p["title"]) for p in playlist]
        self.assertEqual(titles.count(("Peekaboo", "Here With Me")), 1)
        self.assertEqual(
            titles.count(("Roderic, Jacqueline Jones", "Passengers (Original Mix)")),
            1,
        )

    def test_assemble_skips_low_fit(self):
        now = time.time()
        scored = [
            _track("/a.flac", first_seen=now, fit=0.1, verdict="skip", title="Skip"),
            _track("/b.flac", first_seen=now - 10, fit=0.8, title="Keep"),
        ]
        playlist = assemble_playlist(scored, target=300, newest_guarantee=10)
        titles = [p["title"] for p in playlist]
        self.assertIn("Keep", titles)
        self.assertNotIn("Skip", titles)

    def test_normalize_min_fit_accepts_percent(self):
        self.assertAlmostEqual(normalize_min_fit(70), 0.7)
        self.assertAlmostEqual(normalize_min_fit(0.8), 0.8)
        self.assertAlmostEqual(normalize_min_fit(None), 0.6)
        self.assertEqual(normalize_min_fit(-3), 0.0)
        self.assertEqual(normalize_min_fit(200), 1.0)
        self.assertGreaterEqual(KEEP_FIT, 0.6)

    def test_keep_count_is_fit_at_least_sixty(self):
        from sorter.playlist_assemble import _assemble_result

        now = time.time()
        merged = [
            _track("/a.flac", first_seen=now, fit=0.72, title="Lock", verdict="keep"),
            _track("/b.flac", first_seen=now, fit=0.51, title="Soft", verdict="keep"),
            _track("/c.flac", first_seen=now, fit=0.4, title="Spice", verdict="maybe"),
        ]
        result = _assemble_result(
            event_name="Pajamathon 2026",
            brief="test",
            library="Zouk",
            target=300,
            merged=merged,
            min_fit=0.6,
        )
        self.assertEqual(result["keep_count"], 1)
        titles = [t["title"] for t in result["playlist"]]
        self.assertIn("Lock", titles)
        self.assertNotIn("Soft", titles)
        self.assertNotIn("Spice", titles)

    def test_assemble_playlist_honors_min_fit(self):
        now = time.time()
        scored = [
            _track("/high.flac", first_seen=now, fit=0.92, title="High", vibe="Chill", relative_path="Chill/h.flac"),
            _track("/mid.flac", first_seen=now, fit=0.61, title="Mid", vibe="Chill", relative_path="Chill/m.flac"),
            _track("/low.flac", first_seen=now, fit=0.42, title="Low", vibe="Energy", relative_path="Energy/l.flac"),
        ]
        playlist = assemble_playlist(
            scored, target=300, newest_guarantee=8, min_fit=0.7
        )
        titles = [p["title"] for p in playlist]
        self.assertIn("High", titles)
        self.assertNotIn("Mid", titles)
        self.assertNotIn("Low", titles)

    def test_rebalance_latest_uses_min_fit(self):
        from unittest.mock import patch

        now = time.time()
        ranked = [
            _track("/high.flac", first_seen=now, fit=0.9, title="High", vibe="Chill", relative_path="Chill/h.flac"),
            _track("/mid.flac", first_seen=now, fit=0.55, title="Mid", vibe="Energy", relative_path="Energy/m.flac"),
        ]

        class FakeJob:
            event_name = "Pajamathon 2026"
            brief = "test"
            library = "Zouk"
            target = 300
            lane_shares = None
            min_fit = 0.35
            result = {"ranked": ranked, "playlist": ranked, "files": None}

            def to_dict(self):
                return {"id": "x", "result": self.result, "min_fit": self.min_fit}

        job = FakeJob()
        with patch("sorter.playlist_assemble.latest_job", return_value=job):
            out = rebalance_latest_playlist(min_fit=0.8)
        titles = [p["title"] for p in out["result"]["playlist"]]
        self.assertIn("High", titles)
        self.assertNotIn("Mid", titles)
        self.assertAlmostEqual(job.min_fit, 0.8)

    def test_interleave_rotates_lanes(self):
        tracks = [
            {"path": "/c1", "relative_path": "Chill/a.flac", "vibe": "Chill"},
            {"path": "/c2", "relative_path": "Chill/b.flac", "vibe": "Chill"},
            {"path": "/e1", "relative_path": "Energy/a.flac", "vibe": "Energy"},
            {"path": "/r1", "relative_path": "JR&B/a.flac", "genre": "R&B"},
        ]
        out = interleave_by_lane(tracks)
        lanes = [crate_lane(t) for t in out[:3]]
        self.assertEqual(len(set(lanes)), 3)

    def test_crate_lane_from_folder(self):
        self.assertEqual(
            crate_lane({"relative_path": "Chill/Mystical/x.flac", "vibe": "Chill / Mystical"}),
            "chill",
        )
        self.assertEqual(
            crate_lane({"relative_path": "Energy/Light/x.flac", "vibe": "Energy / Light"}),
            "energy",
        )
        self.assertEqual(
            crate_lane({"relative_path": "JR&B/x.flac", "genre": "R&B"}),
            "rnb",
        )
        self.assertEqual(
            crate_lane({"relative_path": "Tribal/x.flac", "vibe": "Tribal"}),
            "tribal",
        )
        self.assertEqual(
            crate_lane({"relative_path": "India/x.flac", "vibe": "India"}),
            "tribal",
        )
        self.assertEqual(
            crate_lane({"relative_path": "Bassy/x.flac", "vibe": "Bassy"}),
            "bassy",
        )
        self.assertEqual(
            crate_lane({"relative_path": "Neo Zouk/x.flac", "vibe": "Neo Zouk"}),
            "neo_zouk",
        )
        self.assertEqual(
            crate_lane({"relative_path": "Beautiful Sound/x.flac", "vibe": "Beautiful Sound"}),
            "beautiful",
        )
        self.assertEqual(
            crate_lane({"relative_path": "Remixes/x.flac", "vibe": "Remixes"}),
            "remixes",
        )
        self.assertEqual(
            crate_lane({"relative_path": "Classics/x.flac", "vibe": "Classics"}),
            "classics",
        )
        self.assertEqual(
            crate_lane({"relative_path": "Nostalgia/x.flac", "vibe": "Nostalgia"}),
            "nostalgia",
        )

    def test_default_shares_include_remixes_classics_nostalgia(self):
        self.assertGreater(LANE_SHARE["remixes"], 0)
        self.assertGreater(LANE_SHARE["classics"], 0)
        self.assertGreater(LANE_SHARE["nostalgia"], 0)
        self.assertAlmostEqual(sum(LANE_SHARE.values()), 1.0, places=2)

    def test_assemble_playlist_drops_low_bitrate(self):
        now = time.time()
        scored = [
            _track("/keep.flac", first_seen=now, fit=0.9, title="Lossless", vibe="Chill", relative_path="Chill/a.flac"),
            _track("/hot.mp3", first_seen=now, fit=0.9, title="ThreeTwenty", vibe="Chill", relative_path="Chill/b.mp3"),
            _track("/thin.mp3", first_seen=now, fit=0.9, title="OneNinetyTwo", vibe="Chill", relative_path="Chill/c.mp3"),
        ]
        scored[1]["bitrate_kbps"] = 320
        scored[2]["bitrate_kbps"] = 192
        playlist = assemble_playlist(scored, target=300, newest_guarantee=4)
        titles = [p["title"] for p in playlist]
        self.assertIn("Lossless", titles)
        self.assertIn("ThreeTwenty", titles)
        self.assertNotIn("OneNinetyTwo", titles)

    def test_assemble_balances_lanes(self):
        now = time.time()
        scored = []
        for i in range(200):
            scored.append(
                _track(
                    f"/zouk/Chill/c{i}.flac",
                    first_seen=now - 1000,
                    fit=0.95,
                    title=f"Chill {i}",
                    vibe="Chill / Mystical",
                    relative_path=f"Chill/Mystical/c{i}.flac",
                )
            )
        for i in range(120):
            scored.append(
                _track(
                    f"/zouk/Energy/e{i}.flac",
                    first_seen=now - 2000,
                    fit=0.8,
                    title=f"Energy {i}",
                    vibe="Energy / Light",
                    relative_path=f"Energy/Light/e{i}.flac",
                )
            )
        for i in range(120):
            scored.append(
                _track(
                    f"/zouk/JRB/r{i}.flac",
                    first_seen=now - 2000,
                    fit=0.8,
                    title=f"Rnb {i}",
                    vibe="JR&B",
                    relative_path=f"JR&B/r{i}.flac",
                )
            )
        playlist = assemble_playlist(scored, target=350, newest_guarantee=10)
        n = len(playlist)
        self.assertGreaterEqual(n, 300)
        chill = sum(1 for p in playlist if crate_lane(p) == "chill")
        energy = sum(1 for p in playlist if crate_lane(p) == "energy")
        rnb = sum(1 for p in playlist if crate_lane(p) == "rnb")
        self.assertLess(chill / n, 0.5)
        self.assertGreaterEqual(energy, 25)
        self.assertGreaterEqual(rnb, 25)

    def test_normalize_lane_shares_accepts_percents(self):
        shares = normalize_lane_shares({"chill": 40, "energy": 20, "rnb": 20, "other": 20})
        self.assertAlmostEqual(sum(shares.values()), 1.0, places=3)
        self.assertGreater(shares["chill"], shares["energy"])
        self.assertEqual(shares["hiphop"], 0.0)

    def test_normalize_lane_shares_keeps_typed_undersum(self):
        shares = normalize_lane_shares({"chill": 20, "rnb": 10})
        self.assertAlmostEqual(shares["chill"], 0.20, places=3)
        self.assertAlmostEqual(shares["rnb"], 0.10, places=3)
        self.assertEqual(shares["energy"], 0.0)
        self.assertLess(sum(shares.values()), 0.31)

    def test_assemble_playlist_respects_custom_shares(self):
        now = time.time()
        scored = []
        for i in range(200):
            scored.append(
                _track(
                    f"/zouk/Chill/c{i}.flac",
                    first_seen=now - 1000,
                    fit=0.95,
                    title=f"Chill {i}",
                    vibe="Chill / Mystical",
                    relative_path=f"Chill/Mystical/c{i}.flac",
                )
            )
        for i in range(200):
            scored.append(
                _track(
                    f"/zouk/Energy/e{i}.flac",
                    first_seen=now - 2000,
                    fit=0.8,
                    title=f"Energy {i}",
                    vibe="Energy / Light",
                    relative_path=f"Energy/Light/e{i}.flac",
                )
            )
        shares = normalize_lane_shares({"energy": 70, "chill": 20, "other": 10})
        playlist = assemble_playlist(
            scored, target=350, newest_guarantee=8, shares=shares
        )
        n = max(1, len(playlist))
        chill = sum(1 for p in playlist if crate_lane(p) == "chill")
        energy = sum(1 for p in playlist if crate_lane(p) == "energy")
        self.assertGreater(energy / n, chill / n)
        self.assertGreaterEqual(energy / n, 0.5)
        self.assertLessEqual(chill / n, 0.35)

    def test_assemble_playlist_uses_exact_genre_percents(self):
        now = time.time()
        scored = []
        for i in range(120):
            scored.append(
                _track(
                    f"/zouk/Chill/c{i}.flac",
                    first_seen=now - 1000,
                    fit=0.9,
                    title=f"Chill {i}",
                    vibe="Chill",
                    relative_path=f"Chill/c{i}.flac",
                )
            )
        for i in range(80):
            scored.append(
                _track(
                    f"/zouk/JRB/r{i}.flac",
                    first_seen=now - 1000,
                    fit=0.9,
                    title=f"Rnb {i}",
                    vibe="JR&B",
                    relative_path=f"JR&B/r{i}.flac",
                )
            )
        for i in range(220):
            scored.append(
                _track(
                    f"/zouk/Energy/e{i}.flac",
                    first_seen=now - 1000,
                    fit=0.85,
                    title=f"Energy {i}",
                    vibe="Energy",
                    relative_path=f"Energy/e{i}.flac",
                )
            )
        shares = normalize_lane_shares({"chill": 20, "rnb": 10})
        playlist = assemble_playlist(
            scored, target=350, newest_guarantee=8, shares=shares
        )
        n = max(1, len(playlist))
        chill = sum(1 for p in playlist if crate_lane(p) == "chill")
        rnb = sum(1 for p in playlist if crate_lane(p) == "rnb")
        self.assertAlmostEqual(chill / n, 0.20, delta=0.08)
        self.assertAlmostEqual(rnb / n, 0.10, delta=0.08)

    def test_rebalance_latest_uses_new_shares(self):
        from unittest.mock import patch

        now = time.time()
        ranked = []
        for i in range(40):
            ranked.append(
                _track(
                    f"/zouk/Chill/c{i}.flac",
                    first_seen=now,
                    fit=0.9,
                    title=f"Chill {i}",
                    vibe="Chill",
                    relative_path=f"Chill/c{i}.flac",
                )
            )
        for i in range(200):
            ranked.append(
                _track(
                    f"/zouk/Energy/e{i}.flac",
                    first_seen=now,
                    fit=0.9,
                    title=f"Energy {i}",
                    vibe="Energy",
                    relative_path=f"Energy/e{i}.flac",
                )
            )

        class FakeJob:
            event_name = "Pajamathon 2026"
            brief = "test"
            library = "Zouk"
            target = 300
            lane_shares = None
            result = {"ranked": ranked, "playlist": ranked[:10], "files": None}

            def to_dict(self):
                return {"id": "x", "result": self.result, "lane_shares": self.lane_shares}

        job = FakeJob()
        with patch("sorter.playlist_assemble.latest_job", return_value=job):
            out = rebalance_latest_playlist(shares={"energy": 80, "chill": 20})
        playlist = out["result"]["playlist"]
        energy = sum(1 for p in playlist if crate_lane(p) == "energy")
        chill = sum(1 for p in playlist if crate_lane(p) == "chill")
        self.assertGreater(energy, chill)
        self.assertAlmostEqual(job.lane_shares["energy"], 0.8, places=2)

    def test_rank_score_boosts_new_over_stale_same_fit(self):
        now = time.time()
        new = _track("/n.flac", first_seen=now, fit=0.6)
        old = _track("/o.flac", first_seen=now - 400 * 86400, fit=0.6)
        self.assertGreater(rank_score(new), rank_score(old))

    def test_dedupe_passengers_across_chill_folders(self):
        tracks = [
            {
                "path": "/Zouk/Chill/Mystical/Roderic, Jacqueline Jones - Passengers (Original Mix).flac",
                "artist": "Roderic, Jacqueline Jones",
                "title": "Passengers (Original Mix)",
                "name": "Roderic, Jacqueline Jones - Passengers (Original Mix).flac",
            },
            {
                "path": "/Zouk/Chill/Journey/Roderic & Jacqueline Jones - Passengers.flac",
                "artist": "Roderic & Jacqueline Jones",
                "title": "Passengers",
                "name": "Roderic & Jacqueline Jones - Passengers.flac",
            },
        ]
        out = dedupe_tracks_for_eval(tracks)
        self.assertEqual(len(out), 1)

    def test_dedupe_keeps_one_clozeee_harmony(self):
        tracks = [
            {
                "path": "/Zouk/Chill/Mystical/CloZee - Harmony.flac",
                "artist": "CloZee",
                "title": "Harmony",
                "name": "CloZee - Harmony.flac",
                "first_seen": 200,
            },
            {
                "path": "/Zouk/Tribal/CloZee - Harmony.flac",
                "artist": "CloZee",
                "title": "Harmony",
                "name": "CloZee - Harmony.flac",
                "first_seen": 100,
            },
        ]
        out = dedupe_tracks_for_eval(tracks)
        self.assertEqual(len(out), 1)
        self.assertIn("Chill", out[0]["path"])

    def test_result_from_cache_rebuilds_lists(self):
        from unittest.mock import patch

        now = time.time()
        tracks = [
            _track(
                "/Zouk/Chill/a.flac",
                first_seen=now,
                fit=0.9,
                artist="Azaleh",
                title="Moonlight",
                vibe="Chill / Mystical",
                relative_path="Chill/Mystical/a.flac",
            )
        ]
        cache = {
            tracks[0]["path"]: {
                "fit": 0.9,
                "verdict": "keep",
                "reason": "cozy",
                "idents": sorted(_track_idents(tracks[0])),
            }
        }
        with patch(
            "sorter.playlist_assemble.list_library_tracks", return_value=tracks
        ), patch(
            "sorter.playlist_assemble.load_score_cache", return_value=cache
        ):
            result = result_from_cache(event_name="Pajamathon 2026", target=300)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["scored_total"], 1)
        self.assertEqual(result["ranked"][0]["title"], "Moonlight")
        self.assertTrue(result["from_cache"])

    def test_score_cache_survives_reload_and_skips_twins(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        scored = {
            "path": "/Zouk/Tribal/CloZee - Harmony.flac",
            "artist": "CloZee",
            "title": "Harmony",
            "name": "CloZee - Harmony.flac",
        }
        payload = {
            scored["path"]: {
                "fit": 0.25,
                "verdict": "skip",
                "reason": "too aggressive",
                "heard": True,
                "idents": sorted(_track_idents(scored)),
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "playlist_assemble_scores.json"
            with patch("sorter.playlist_assemble.CACHE_PATH", cache_path):
                save_score_cache("Pajamathon 2026", payload)
                self.assertTrue(cache_path.is_file())
                loaded = load_score_cache("Pajamathon 2026")
                self.assertIn(scored["path"], loaded)
                twin = {
                    "path": "/Zouk/Chill/Mystical/CloZee - Harmony.flac",
                    "artist": "CloZee",
                    "title": "Harmony",
                    "name": "CloZee - Harmony.flac",
                }
                self.assertTrue(_cache_covers_track(loaded, twin))
                raw = json.loads(cache_path.read_text(encoding="utf-8"))
                self.assertIn("pajamathon-2026", raw)

    def test_cache_covers_same_song_other_folder(self):
        scored = {
            "path": "/Zouk/Tribal/CloZee - Harmony.flac",
            "artist": "CloZee",
            "title": "Harmony",
            "name": "CloZee - Harmony.flac",
        }
        from sorter.playlist_assemble import _track_idents

        cache = {
            scored["path"]: {
                "fit": 0.25,
                "verdict": "skip",
                "idents": sorted(_track_idents(scored)),
            }
        }
        twin = {
            "path": "/Zouk/Chill/Mystical/CloZee - Harmony.flac",
            "artist": "CloZee",
            "title": "Harmony",
            "name": "CloZee - Harmony.flac",
        }
        self.assertTrue(_cache_covers_track(cache, twin))
        merged = _merge_scored([scored, twin], cache)
        self.assertEqual(len(merged), 1)

    def test_clip_start_skips_intro_on_long_tracks(self):
        self.assertEqual(clip_start_sec(None), 0.0)
        self.assertEqual(clip_start_sec(20), 0.0)
        self.assertGreaterEqual(clip_start_sec(240), 18.0)
        self.assertLess(clip_start_sec(240), 240 - 28)

    def test_extract_listen_clip_calls_ffmpeg(self):
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "song.flac"
            src.write_bytes(b"x")
            out_dir = Path(tmp) / "clips"

            def fake_run(cmd, check=False, capture_output=True, timeout=None):
                dest = Path(cmd[-1])
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"m" * 2000)

                class P:
                    returncode = 0
                    stderr = b""

                return P()

            with patch("sorter.playlist_assemble.subprocess.run", side_effect=fake_run):
                clip = extract_listen_clip(src, start_sec=40, out_dir=out_dir)
            self.assertTrue(clip.is_file())
            self.assertGreater(clip.stat().st_size, 800)

    def test_extract_listen_clip_times_out(self):
        import subprocess
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "song.flac"
            src.write_bytes(b"x")

            def hang(*_args, **_kwargs):
                raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=25)

            with patch("sorter.playlist_assemble.subprocess.run", side_effect=hang):
                with self.assertRaises(RuntimeError):
                    extract_listen_clip(src, start_sec=0, out_dir=Path(tmp) / "clips")

    def test_default_event_is_pajamathon(self):
        self.assertIn("pajama", DEFAULT_EVENT["name"].lower())
        self.assertIn("pajama", DEFAULT_EVENT["brief"].lower())
        self.assertEqual(slug_event("Pajamathon 2026"), "pajamathon-2026")
        self.assertEqual(event_folder_name("Pajamathon"), "Pajamathon 2026")
        self.assertEqual(event_folder_name("Pajamathon 2026"), "Pajamathon 2026")

    def test_materialize_set_directory_writes_real_files(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            src_root = Path(tmp) / "zouk"
            dest_root = Path(tmp) / "Sets"
            src_root.mkdir()
            dest_root.mkdir()
            a = src_root / "warm.flac"
            b = src_root / "groove.flac"
            a.write_bytes(b"aaa")
            b.write_bytes(b"bbb")
            playlist = [
                {
                    "path": str(a),
                    "artist": "Azaleh",
                    "title": "Moonlight",
                    "name": "warm.flac",
                },
                {
                    "path": str(b),
                    "artist": "Saia",
                    "title": "Slow",
                    "name": "groove.flac",
                },
            ]
            out = materialize_set_directory(
                playlist, event_name="Pajamathon", sets_root=dest_root
            )
            folder = Path(out["folder"])
            self.assertEqual(folder.name, "Pajamathon 2026")
            self.assertTrue(folder.is_dir())
            files = sorted(p.name for p in folder.iterdir() if p.is_file())
            self.assertEqual(out["count"], 2)
            self.assertTrue(any(n.startswith("001.") and "Moonlight" in n for n in files))
            self.assertTrue(any(n.startswith("002.") and "Slow" in n for n in files))
            copied = list(folder.glob("001.*"))[0]
            self.assertEqual(copied.read_bytes(), b"aaa")

    def test_materialize_removes_previous_numbered_copies_of_same_song(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            src_root = Path(tmp) / "zouk"
            dest_root = Path(tmp) / "Sets"
            src_root.mkdir()
            dest_root.mkdir()
            src = src_root / "01 Dusk Till Dawn - Kizomba Remix.m4a"
            src.write_bytes(b"dusk")
            folder = dest_root / "Pajamathon 2026"
            folder.mkdir()
            leftover_a = folder / "405. 01 Dusk Till Dawn - Kizomba Remix.m4a"
            leftover_b = folder / "090. Vlad Ivan - Dusk Till Dawn - Kizomba Remix.m4a"
            leftover_a.write_bytes(b"old-a")
            leftover_b.write_bytes(b"old-b")
            Path(f"{leftover_a}.vdjstems").write_bytes(b"stems")
            keep = folder / "100. Someone Else - Other Song.m4a"
            keep.write_bytes(b"keep")
            out = materialize_set_directory(
                [
                    {
                        "path": str(src),
                        "artist": "Vlad Ivan",
                        "title": "Dusk Till Dawn - Kizomba Remix",
                        "name": src.name,
                    }
                ],
                event_name="Pajamathon",
                sets_root=dest_root,
                clone_cues=False,
            )
            folder = Path(out["folder"])
            names = sorted(p.name for p in folder.iterdir() if p.suffix.lower() == ".m4a")
            self.assertEqual(out["count"], 1)
            self.assertEqual(len(names), 2)
            self.assertTrue(keep.exists())
            self.assertFalse(leftover_a.exists())
            self.assertFalse(leftover_b.exists())
            self.assertFalse(Path(f"{leftover_a}.vdjstems").exists())
            self.assertTrue(
                any(n.startswith("001.") and "Dusk Till Dawn" in n for n in names)
            )
            self.assertEqual(out.get("removed_duplicates"), 2)

    def test_materialize_keeps_unrelated_song_with_overlapping_words(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            src_root = Path(tmp) / "zouk"
            dest_root = Path(tmp) / "Sets"
            src_root.mkdir()
            dest_root.mkdir()
            src = src_root / "01 Dusk Till Dawn - Kizomba Remix.m4a"
            src.write_bytes(b"dusk")
            folder = dest_root / "Pajamathon 2026"
            folder.mkdir()
            other = folder / "048. alayna - Between Dusk And Dawn.flac"
            other.write_bytes(b"other")
            out = materialize_set_directory(
                [
                    {
                        "path": str(src),
                        "artist": "Vlad Ivan",
                        "title": "Dusk Till Dawn - Kizomba Remix",
                        "name": src.name,
                    }
                ],
                event_name="Pajamathon",
                sets_root=dest_root,
                clone_cues=False,
            )
            self.assertTrue(other.exists())
            self.assertEqual(out.get("removed_duplicates"), 0)

    def test_clone_cues_skips_when_vdj_open(self):
        from unittest.mock import patch

        with patch("sorter.relocate.is_virtualdj_running", return_value=True):
            out = clone_cues_for_set_paths([("/a.flac", "/b.flac")])
        self.assertTrue(out["skipped_vdj_open"])
        self.assertEqual(out["cloned"], 0)

    def test_clone_cues_copies_pois_to_new_path(self):
        import tempfile

        xml = """<?xml version="1.0" encoding="UTF-8"?>
<VirtualDJ_Database Version="2021">
 <Song FilePath="/Music/DJ/Music/Zouk/Chill/Mystical/src.flac">
  <Tags Author="Azaleh" Title="Moonlight"/>
  <Poi Name="Intro" Pos="8.0" Type="cue" Num="1" />
  <Poi Name="Drop" Pos="64.0" Type="cue" Num="2" />
  <Poi Name="dl" Pos="32.0" Type="loop" Num="-1" />
 </Song>
</VirtualDJ_Database>
"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            db.write_text(xml, encoding="utf-8")
            from unittest.mock import patch

            with patch("sorter.relocate.is_virtualdj_running", return_value=False), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                out = clone_cues_for_set_paths(
                    [
                        (
                            "/Music/DJ/Music/Zouk/Chill/Mystical/src.flac",
                            "/Music/DJ/Music/Sets/Pajamathon 2026/001. Moonlight.flac",
                        )
                    ],
                    database_path=db,
                )
            self.assertEqual(out["cloned"], 1)
            text = db.read_text(encoding="utf-8")
            self.assertEqual(text.count("<Song"), 2)
            self.assertIn('FilePath="/Music/DJ/Music/Sets/Pajamathon 2026/001. Moonlight.flac"', text)
            dest = text[text.find("Pajamathon") :]
            self.assertIn('Name="Intro"', dest)
            self.assertIn('Name="Drop"', dest)
            self.assertIn('Type="loop"', dest)
            self.assertIn('User2="Chill/Mystical"', dest)

    def test_clone_cues_refreshes_thin_dest_entry(self):
        import tempfile
        from unittest.mock import patch

        xml = """<?xml version="1.0" encoding="UTF-8"?>
<VirtualDJ_Database Version="2021">
 <Song FilePath="/lib/src.flac">
  <Tags Author="Azaleh" Title="Moonlight"/>
  <Poi Name="Intro" Pos="8.0" Type="cue" Num="1" />
  <Poi Name="Drop" Pos="64.0" Type="cue" Num="2" />
 </Song>
 <Song FilePath="/Sets/Pajamathon 2026/001. Moonlight.flac">
  <Tags TrackNumber="001"/>
  <Poi Type="automix" Point="realStart" />
 </Song>
</VirtualDJ_Database>
"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            db.write_text(xml, encoding="utf-8")
            with patch("sorter.relocate.is_virtualdj_running", return_value=False), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                out = clone_cues_for_set_paths(
                    [("/lib/src.flac", "/Sets/Pajamathon 2026/001. Moonlight.flac")],
                    database_path=db,
                )
            self.assertEqual(out["replaced"], 1)
            text = db.read_text(encoding="utf-8")
            self.assertEqual(text.count("<Song"), 2)
            dest = text[text.find("Pajamathon") :]
            self.assertIn('Name="Intro"', dest)
            self.assertIn('Name="Drop"', dest)

    def test_clone_cues_sets_directory_sort_from_source_folder(self):
        import tempfile
        from unittest.mock import patch

        xml = """<?xml version="1.0" encoding="UTF-8"?>
<VirtualDJ_Database Version="2021">
 <Song FilePath="/Music/DJ/Music/Zouk/Tribal/src.flac">
  <Tags Author="CloZee" Title="Harmony" User2="Tribal"/>
  <Poi Name="Intro" Pos="8.0" Type="cue" Num="1" />
 </Song>
 <Song FilePath="/Music/DJ/Music/Sets/Pajamathon 2026/001. Harmony.flac">
  <Tags Author="CloZee" Title="Harmony" User2="Pajamathon 2026"/>
  <Poi Name="Intro" Pos="8.0" Type="cue" Num="1" />
 </Song>
</VirtualDJ_Database>
"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            db.write_text(xml, encoding="utf-8")
            with patch("sorter.relocate.is_virtualdj_running", return_value=False), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                out = clone_cues_for_set_paths(
                    [
                        (
                            "/Music/DJ/Music/Zouk/Tribal/src.flac",
                            "/Music/DJ/Music/Sets/Pajamathon 2026/001. Harmony.flac",
                        )
                    ],
                    database_path=db,
                )
            self.assertEqual(out["replaced"], 1)
            dest = db.read_text(encoding="utf-8")
            dest = dest[dest.find("Pajamathon") :]
            self.assertIn('User2="Tribal"', dest)
            self.assertNotIn('User2="Pajamathon 2026"', dest)

    def test_stage_uncued_playlist_tracks_copies_only_uncued(self):
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / "lib"
            add = Path(tmp) / "Add Cues"
            lib.mkdir()
            add.mkdir()
            cued = lib / "cued.flac"
            bare = lib / "bare.flac"
            cued.write_bytes(b"ccc")
            bare.write_bytes(b"bbb")
            xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<VirtualDJ_Database Version="2021">
 <Song FilePath="{cued}">
  <Poi Name="Intro" Pos="8.0" Type="cue" Num="1" />
 </Song>
 <Song FilePath="{bare}">
  <Poi Type="automix" Point="realStart" />
 </Song>
</VirtualDJ_Database>
"""
            db = Path(tmp) / "database.xml"
            db.write_text(xml, encoding="utf-8")
            with patch("sorter.relocate.is_virtualdj_running", return_value=False), patch(
                "vdj_database_safety.is_virtualdj_running", return_value=False
            ):
                out = stage_uncued_playlist_tracks(
                    [(str(cued), "/set/001.flac"), (str(bare), "/set/002.flac")],
                    folder_name="Pajamathon",
                    add_cues_root=add,
                    database_path=db,
                    clone_db=False,
                )
            self.assertEqual(out["staged"], 1)
            self.assertEqual(out["skipped_cued"], 1)
            dest = Path(out["folder"]) / "bare.flac"
            self.assertTrue(dest.is_file())
            self.assertFalse((Path(out["folder"]) / "cued.flac").exists())
            self.assertEqual(dest.read_bytes(), b"bbb")

    def test_tracks_from_score_cache_rebuilds_rows(self):
        cache = {
            "/Zouk/Chill/Azaleh - Moonlight.flac": {
                "fit": 0.9,
                "verdict": "keep",
                "reason": "cozy",
                "artist": "Azaleh",
                "title": "Moonlight",
                "vibe": "Chill / Mystical",
                "relative_path": "Chill/Mystical/Azaleh - Moonlight.flac",
            }
        }
        rows = tracks_from_score_cache(cache)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["artist"], "Azaleh")
        self.assertEqual(rows[0]["title"], "Moonlight")
        self.assertEqual(rows[0]["vibe"], "Chill / Mystical")

    def test_result_from_cache_uses_entries_when_library_empty(self):
        from unittest.mock import patch

        cache = {
            "/Zouk/Chill/Azaleh - Moonlight.flac": {
                "fit": 0.91,
                "verdict": "keep",
                "reason": "cozy midnight groove",
                "idents": ["core:azaleh moonlight"],
                "artist": "Azaleh",
                "title": "Moonlight",
                "vibe": "Chill / Mystical",
            }
        }
        with patch(
            "sorter.playlist_assemble.list_library_tracks", return_value=[]
        ), patch(
            "sorter.playlist_assemble.load_score_cache", return_value=cache
        ):
            result = result_from_cache(event_name="Pajamathon 2026", target=300)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["ranked"][0]["title"], "Moonlight")
        self.assertTrue(result["from_cache"])
        self.assertGreaterEqual(result["scored_total"], 1)

    def test_preview_library_lists_once_and_includes_cache_result(self):
        from unittest.mock import patch

        now = time.time()
        tracks = [
            _track(
                "/Zouk/Chill/a.flac",
                first_seen=now,
                fit=0.9,
                artist="Azaleh",
                title="Moonlight",
                vibe="Chill / Mystical",
            )
        ]
        cache = {
            tracks[0]["path"]: {
                "fit": 0.9,
                "verdict": "keep",
                "reason": "cozy",
                "idents": sorted(_track_idents(tracks[0])),
                "artist": "Azaleh",
                "title": "Moonlight",
                "vibe": "Chill / Mystical",
            }
        }
        with patch(
            "sorter.playlist_assemble.list_library_tracks", return_value=tracks
        ) as listed, patch(
            "sorter.playlist_assemble.load_score_cache", return_value=cache
        ):
            out = preview_library("Zouk")
        self.assertEqual(listed.call_count, 1)
        self.assertEqual(out["cached_evals"], 1)
        self.assertIsNotNone(out["result"])
        self.assertEqual(out["result"]["ranked"][0]["title"], "Moonlight")

    def test_start_assemble_job_seeds_cached_lists_before_scan(self):
        import threading
        from unittest.mock import patch

        release = threading.Event()
        track = _track(
            "/Zouk/Chill/Moonlight.flac",
            first_seen=time.time(),
            fit=0.91,
            artist="Azaleh",
            title="Moonlight",
            vibe="Chill / Mystical",
        )
        cache = {
            track["path"]: {
                "fit": 0.91,
                "verdict": "keep",
                "reason": "cozy midnight groove",
                "idents": sorted(_track_idents(track)),
                "artist": "Azaleh",
                "title": "Moonlight",
                "vibe": "Chill / Mystical",
                "relative_path": "Chill/Mystical/Moonlight.flac",
            }
        }

        def slow_list(_library):
            release.wait(timeout=3)
            return [track]

        with patch(
            "sorter.playlist_assemble.load_score_cache", return_value=cache
        ), patch(
            "sorter.playlist_assemble.save_score_cache"
        ), patch(
            "sorter.playlist_assemble.list_library_tracks", side_effect=slow_list
        ), patch(
            "sorter.playlist_assemble.write_playlist_files",
            return_value={"ok": True, "folder": "", "count": 0},
        ):
            job = start_assemble_job(
                event_name="Pajamathon 2026", use_gemini=False
            )
            try:
                self.assertIsNotNone(job.result)
                assert job.result is not None
                self.assertTrue(job.result.get("from_cache"))
                titles = [t.get("title") for t in job.result["ranked"]]
                self.assertIn("Moonlight", titles)
                self.assertGreaterEqual(len(job.result["ranked"]), 1)
                pl_titles = [t.get("title") for t in job.result["playlist"]]
                self.assertIn("Moonlight", pl_titles)
            finally:
                release.set()
                deadline = time.time() + 2
                while job.status in {"queued", "running"} and time.time() < deadline:
                    time.sleep(0.02)


# Screenshot mix the user selected and asked to keep.
_USER_MIX_PCTS = {
    "chill": 15,
    "energy": 12,
    "rnb": 11,
    "kizouk": 8,
    "lamba": 8,
    "trancy": 5,
    "hiphop": 4,
    "remixes": 9,
    "tribal": 3,
    "bassy": 4,
    "experimental": 0,
    "intense": 0,
    "beautiful": 3,
    "classics": 5,
    "neo_zouk": 0,
    "pop": 0,
    "nostalgia": 5,
    "reggaeton": 0,
    "trippy": 0,
    "world": 0,
    "other": 8,
}


class MixPrefsPersistTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from unittest.mock import patch

        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "playlist_assemble_mix.json"
        self._patcher = patch("sorter.playlist_assemble.MIX_PREFS_PATH", self.path)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()

    def test_load_missing_returns_factory_not_saved(self):
        prefs = load_mix_prefs()
        self.assertFalse(prefs["saved"])
        self.assertEqual(prefs["lane_shares"], dict(LANE_SHARE))
        self.assertEqual(prefs["min_fit"], DEFAULT_MIN_FIT)

    def test_save_and_load_keeps_typed_percents(self):
        saved = save_mix_prefs(_USER_MIX_PCTS, min_fit=60)
        self.assertTrue(saved["saved"])
        self.assertAlmostEqual(saved["lane_shares"]["chill"], 0.15)
        self.assertAlmostEqual(saved["lane_shares"]["energy"], 0.12)
        self.assertAlmostEqual(saved["lane_shares"]["rnb"], 0.11)
        self.assertAlmostEqual(saved["lane_shares"]["remixes"], 0.09)
        self.assertAlmostEqual(saved["lane_shares"]["tribal"], 0.03)
        self.assertAlmostEqual(saved["lane_shares"]["bassy"], 0.04)
        self.assertAlmostEqual(saved["lane_shares"]["beautiful"], 0.03)
        self.assertAlmostEqual(saved["lane_shares"]["nostalgia"], 0.05)
        self.assertAlmostEqual(saved["lane_shares"]["other"], 0.08)
        self.assertAlmostEqual(saved["min_fit"], 0.60)
        self.assertAlmostEqual(sum(saved["lane_shares"].values()), 1.0, places=3)
        self.assertTrue(self.path.is_file())
        loaded = load_mix_prefs()
        self.assertTrue(loaded["saved"])
        self.assertEqual(loaded["lane_shares"], saved["lane_shares"])
        self.assertNotAlmostEqual(loaded["lane_shares"]["chill"], LANE_SHARE["chill"])

    def test_corrupt_file_falls_back_to_factory(self):
        self.path.write_text("not-json", encoding="utf-8")
        prefs = load_mix_prefs()
        self.assertFalse(prefs["saved"])
        self.assertEqual(prefs["lane_shares"]["chill"], LANE_SHARE["chill"])

    def test_preview_includes_saved_mix_prefs(self):
        from unittest.mock import patch

        save_mix_prefs(_USER_MIX_PCTS, min_fit=60)
        with patch(
            "sorter.playlist_assemble.list_library_tracks", return_value=[]
        ), patch("sorter.playlist_assemble.load_score_cache", return_value={}):
            out = preview_library("Zouk")
        self.assertTrue(out["mix_prefs"]["saved"])
        self.assertAlmostEqual(out["mix_prefs"]["lane_shares"]["chill"], 0.15)
        self.assertAlmostEqual(out["mix_prefs"]["lane_shares"]["remixes"], 0.09)
        self.assertAlmostEqual(out["defaults"]["lane_shares"]["chill"], LANE_SHARE["chill"])

    def test_resolve_mix_uses_saved_when_request_omits_shares(self):
        save_mix_prefs(_USER_MIX_PCTS, min_fit=60)
        shares, min_fit = resolve_mix(None, None)
        self.assertAlmostEqual(shares["chill"], 0.15)
        self.assertAlmostEqual(shares["tribal"], 0.03)
        self.assertAlmostEqual(min_fit, 0.60)

    def test_resolve_mix_prefers_request_over_saved(self):
        save_mix_prefs(_USER_MIX_PCTS, min_fit=60)
        shares, min_fit = resolve_mix({"chill": 20, "rnb": 10}, 70)
        self.assertAlmostEqual(shares["chill"], 0.20)
        self.assertAlmostEqual(shares["rnb"], 0.10)
        self.assertAlmostEqual(min_fit, 0.70)

    def test_rebalance_persists_selected_mix(self):
        from unittest.mock import patch

        now = time.time()
        ranked = [
            _track(
                "/zouk/Chill/c0.flac",
                first_seen=now,
                fit=0.9,
                title="Chill 0",
                vibe="Chill",
                relative_path="Chill/c0.flac",
            )
        ]

        class FakeJob:
            event_name = "Pajamathon 2026"
            brief = "test"
            library = "Zouk"
            target = 300
            lane_shares = None
            min_fit = 0.6
            result = {"ranked": ranked, "playlist": ranked, "files": None}

            def to_dict(self):
                return {"id": "x", "result": self.result, "lane_shares": self.lane_shares}

        job = FakeJob()
        with patch("sorter.playlist_assemble.latest_job", return_value=job):
            rebalance_latest_playlist(shares=_USER_MIX_PCTS, min_fit=60)
        loaded = load_mix_prefs()
        self.assertTrue(loaded["saved"])
        self.assertAlmostEqual(loaded["lane_shares"]["chill"], 0.15)
        self.assertAlmostEqual(loaded["lane_shares"]["remixes"], 0.09)

    def test_start_assemble_job_persists_selected_mix(self):
        from unittest.mock import patch

        with patch("sorter.playlist_assemble.load_score_cache", return_value={}), patch(
            "sorter.playlist_assemble.list_library_tracks", return_value=[]
        ), patch("sorter.playlist_assemble.save_score_cache"), patch(
            "sorter.playlist_assemble.write_playlist_files",
            return_value={"ok": True, "folder": "", "count": 0},
        ):
            job = start_assemble_job(
                event_name="Pajamathon 2026",
                use_gemini=False,
                lane_shares=_USER_MIX_PCTS,
                min_fit=60,
            )
        loaded = load_mix_prefs()
        self.assertTrue(loaded["saved"])
        self.assertAlmostEqual(loaded["lane_shares"]["chill"], 0.15)
        self.assertAlmostEqual(job.lane_shares["chill"], 0.15)
        self.assertAlmostEqual(job.min_fit, 0.60)


class AssembleJobSnapshotTests(unittest.TestCase):
    def setUp(self):
        from sorter import playlist_assemble as pa

        self._jobs_backup = dict(pa._jobs)
        self._live_backup = set(pa._live_workers)
        with pa._jobs_lock:
            pa._jobs.clear()
        pa._live_workers.clear()

    def tearDown(self):
        from sorter import playlist_assemble as pa

        with pa._jobs_lock:
            pa._jobs.clear()
            pa._jobs.update(self._jobs_backup)
        pa._live_workers.clear()
        pa._live_workers.update(self._live_backup)

    def test_persist_and_load_job_snapshot(self):
        job = AssembleJob(
            id="abc123",
            status="running",
            created_at=time.time(),
            event_name="Pajamathon 2026",
            brief="test",
            library="Zouk",
            chunk_size=12,
            target=400,
            message="Hearing chunk 2/229",
            scored=338,
            kept=221,
        )
        persist_job(job)
        loaded = load_job_snapshot()
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.id, "abc123")
        self.assertEqual(loaded.scored, 338)
        self.assertIn("Hearing chunk", loaded.message)

    def test_orphaned_running_snapshot_unsticks(self):
        from sorter import playlist_assemble as pa

        job = AssembleJob(
            id="deadjob",
            status="running",
            created_at=time.time(),
            event_name="Pajamathon 2026",
            brief="test",
            library="Zouk",
            chunk_size=12,
            target=400,
            message="Chunk 1/229 · 338 scored",
            scored=338,
        )
        persist_job(job)
        with pa._jobs_lock:
            pa._jobs.clear()
        pa._live_workers.clear()
        latest = latest_job()
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertNotIn(latest.status, {"running", "queued"})
        self.assertIn("Assemble", latest.message)

    def test_live_running_job_is_not_unstuck(self):
        from sorter import playlist_assemble as pa

        job = AssembleJob(
            id="livejob",
            status="running",
            created_at=time.time(),
            event_name="Pajamathon 2026",
            brief="test",
            library="Zouk",
            chunk_size=12,
            target=400,
            message="Hearing chunk 2/229",
        )
        with pa._jobs_lock:
            pa._jobs[job.id] = job
        pa._live_workers.add(job.id)
        latest = latest_job()
        self.assertEqual(latest.status, "running")
        self.assertEqual(latest.id, "livejob")


class AssembleSkipsTransitionsFolderTests(unittest.TestCase):
    def test_path_is_assemble_excluded_matches_transitions_folder_only(self):
        self.assertTrue(
            path_is_assemble_excluded("/Music/DJ/Music/Zouk/Transitions/Natural High.flac")
        )
        self.assertTrue(
            path_is_assemble_excluded(
                "/Music/DJ/Music/Zouk/Transitions/Chill Sunday Kizouk House Set/StillU.flac"
            )
        )
        self.assertTrue(
            path_is_assemble_excluded(
                "/Music/DJ/Music/Ecstatic Dance/30 Utility - Transitions/TwilightTime.flac"
            )
        )
        self.assertFalse(
            path_is_assemble_excluded("/Music/DJ/Music/Zouk/Chill/Natural High.flac")
        )
        self.assertFalse(
            path_is_assemble_excluded("/Music/DJ/Music/Zouk/Nostalgia/Stay Forever.wav")
        )

    def test_assemble_playlist_drops_transitions_even_when_fit_is_high(self):
        now = time.time()
        scored = [
            _track(
                "/Zouk/Transitions/Natural High.flac",
                first_seen=now,
                fit=0.99,
                title="Natural High",
                vibe="Transitions",
                relative_path="Transitions/Natural High.flac",
            ),
            _track(
                "/Zouk/Chill/Keep Me.flac",
                first_seen=now,
                fit=0.9,
                title="Keep Me",
                vibe="Chill",
                relative_path="Chill/Keep Me.flac",
            ),
        ]
        playlist = assemble_playlist(scored, target=300, newest_guarantee=4)
        titles = [p["title"] for p in playlist]
        self.assertIn("Keep Me", titles)
        self.assertNotIn("Natural High", titles)

    def test_assemble_playlist_keeps_same_song_outside_transitions(self):
        now = time.time()
        scored = [
            _track(
                "/Zouk/Transitions/Natural High.flac",
                first_seen=now,
                fit=0.99,
                artist="Tool",
                title="Natural High",
                relative_path="Transitions/Natural High.flac",
            ),
            _track(
                "/Zouk/Chill/Natural High.flac",
                first_seen=now,
                fit=0.88,
                artist="Tool",
                title="Natural High",
                relative_path="Chill/Natural High.flac",
            ),
        ]
        playlist = assemble_playlist(scored, target=300, newest_guarantee=4)
        paths = [p["path"] for p in playlist]
        self.assertIn("/Zouk/Chill/Natural High.flac", paths)
        self.assertNotIn("/Zouk/Transitions/Natural High.flac", paths)

    def test_list_library_tracks_skips_transitions_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Zouk"
            (root / "Chill").mkdir(parents=True)
            (root / "Transitions" / "Goth").mkdir(parents=True)
            (root / "Chill" / "ok.flac").write_bytes(b"ok")
            (root / "Transitions" / "tool.wav").write_bytes(b"tool")
            (root / "Transitions" / "Goth" / "edit.flac").write_bytes(b"edit")
            with patch("sorter.playlist_assemble.LIBRARIES", {"Zouk": root}), patch(
                "sorter.playlist_assemble._db_meta_for_root", return_value={}
            ):
                tracks = list_library_tracks("Zouk")
        names = {Path(t["path"]).name for t in tracks}
        self.assertEqual(names, {"ok.flac"})

    def test_tracks_from_score_cache_drops_transitions(self):
        cache = {
            "/Zouk/Transitions/TwilightTime.flac": {
                "fit": 0.9,
                "verdict": "keep",
                "relative_path": "Transitions/TwilightTime.flac",
                "title": "TwilightTime",
                "artist": "X",
            },
            "/Zouk/Chill/Stay.flac": {
                "fit": 0.9,
                "verdict": "keep",
                "relative_path": "Chill/Stay.flac",
                "title": "Stay",
                "artist": "Y",
            },
        }
        rows = tracks_from_score_cache(cache)
        paths = [r["path"] for r in rows]
        self.assertIn("/Zouk/Chill/Stay.flac", paths)
        self.assertNotIn("/Zouk/Transitions/TwilightTime.flac", paths)

    def test_assemble_result_ranked_omits_transitions(self):
        from sorter.playlist_assemble import _assemble_result

        now = time.time()
        merged = [
            _track(
                "/Zouk/Transitions/StillU.flac",
                first_seen=now,
                fit=0.95,
                title="StillU",
                relative_path="Transitions/StillU.flac",
            ),
            _track(
                "/Zouk/Chill/Lock.flac",
                first_seen=now,
                fit=0.8,
                title="Lock",
                relative_path="Chill/Lock.flac",
            ),
        ]
        result = _assemble_result(
            event_name="Pajamathon 2026",
            brief="test",
            library="Zouk",
            target=300,
            merged=merged,
        )
        ranked_titles = [t["title"] for t in result["ranked"]]
        self.assertIn("Lock", ranked_titles)
        self.assertNotIn("StillU", ranked_titles)
        self.assertFalse(any(track_is_assemble_excluded(t) for t in result["playlist"]))

    def test_materialize_skips_transitions_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            src_root = Path(tmp) / "Zouk"
            trans = src_root / "Transitions"
            chill = src_root / "Chill"
            dest_root = Path(tmp) / "Sets"
            trans.mkdir(parents=True)
            chill.mkdir(parents=True)
            dest_root.mkdir()
            blocked = trans / "Natural High.flac"
            keep = chill / "Keep Me.flac"
            blocked.write_bytes(b"nope")
            keep.write_bytes(b"yes")
            out = materialize_set_directory(
                [
                    {
                        "path": str(blocked),
                        "artist": "",
                        "title": "Natural High",
                        "name": "Natural High.flac",
                    },
                    {
                        "path": str(keep),
                        "artist": "A",
                        "title": "Keep Me",
                        "name": "Keep Me.flac",
                    },
                ],
                event_name="Pajamathon",
                sets_root=dest_root,
                clone_cues=False,
            )
            names = [Path(t["path"]).name for t in out["tracks"]]
            self.assertEqual(out["count"], 1)
            self.assertTrue(any("Keep Me" in n for n in names))
            self.assertFalse(any("Natural High" in n for n in names))

    def test_job_snapshot_strips_transitions_from_saved_playlist(self):
        now = time.time()
        raw = {
            "id": "snap1",
            "status": "ok",
            "created_at": now,
            "event_name": "Pajamathon 2026",
            "brief": "test",
            "library": "Zouk",
            "chunk_size": 12,
            "target": 400,
            "result": {
                "playlist": [
                    _track(
                        "/Zouk/Transitions/TwilightTime.flac",
                        first_seen=now,
                        fit=0.9,
                        title="TwilightTime",
                        relative_path="Transitions/TwilightTime.flac",
                    ),
                    _track(
                        "/Zouk/Chill/Lock.flac",
                        first_seen=now,
                        fit=0.9,
                        title="Lock",
                        relative_path="Chill/Lock.flac",
                    ),
                ],
                "ranked": [
                    _track(
                        "/Zouk/Transitions/TwilightTime.flac",
                        first_seen=now,
                        fit=0.9,
                        title="TwilightTime",
                        relative_path="Transitions/TwilightTime.flac",
                    )
                ],
            },
        }
        job = job_from_dict(raw)
        titles = [t["title"] for t in (job.result or {}).get("playlist") or []]
        ranked = [t["title"] for t in (job.result or {}).get("ranked") or []]
        self.assertEqual(titles, ["Lock"])
        self.assertNotIn("TwilightTime", ranked)


if __name__ == "__main__":
    unittest.main()
