"""Tests for Best-for-set practice_scores listing + priority overlay."""

from __future__ import annotations

from pathlib import Path

from sorter.transitions_db import (
    _ensure_score_columns,
    connect,
    list_best_practice_scores,
    save_practice_score,
    update_practice_score,
)


def _seed(db: Path, rows: list[dict]) -> None:
    for r in rows:
        save_practice_score(r, db_path=db)


def test_migrate_priority_column(tmp_path: Path):
    db = tmp_path / "p.db"
    conn = connect(db)
    cols_before = {
        r[1] for r in conn.execute("PRAGMA table_info(practice_scores)").fetchall()
    }
    assert "priority" not in cols_before
    _ensure_score_columns(conn)
    conn.commit()
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(practice_scores)").fetchall()
    }
    conn.close()
    assert "priority" in cols


def test_list_best_sort_and_filter_uses_overall_and_priority(tmp_path: Path):
    db = tmp_path / "best.db"
    base = "/Users/kirilllanger/Music/Mixes"
    _seed(
        db,
        [
            {
                "mix_path": f"{base}/pj2026-a.wav",
                "from_track": "A1",
                "to_track": "A2",
                "transition_index": 0,
                "at_sec": 10,
                "overall": 9.0,
                "save_for_set": 0,
                "analyzed_at": "2026-08-01T10:00:00",
            },
            {
                "mix_path": f"{base}/pj2026-b.wav",
                "from_track": "B1",
                "to_track": "B2",
                "transition_index": 0,
                "at_sec": 20,
                "overall": 8.0,
                "save_for_set": 1,
                "analyzed_at": "2026-08-02T10:00:00",
            },
            {
                "mix_path": f"{base}/pj2026-c.wav",
                "from_track": "C1",
                "to_track": "C2",
                "transition_index": 0,
                "at_sec": 30,
                "overall": 6.0,
                "save_for_set": 0,
                "analyzed_at": "2026-08-03T10:00:00",
            },
            {
                "mix_path": f"{base}/other-mix.wav",
                "from_track": "X1",
                "to_track": "X2",
                "transition_index": 0,
                "at_sec": 40,
                "overall": 9.5,
                "save_for_set": 1,
                "analyzed_at": "2026-08-04T10:00:00",
            },
            {
                "mix_path": f"{base}/pj2026-d.wav",
                "from_track": "D1",
                "to_track": "D2",
                "transition_index": 0,
                "at_sec": 50,
                "overall": 5.0,
                "save_for_set": 0,
                "analyzed_at": "2026-08-05T10:00:00",
            },
        ],
    )
    # Prioritize the weak overall row so it still appears via priority >= 1
    update_practice_score(
        mix_path=f"{base}/pj2026-d.wav",
        transition_index=0,
        priority=5,
        db_path=db,
    )
    # Mid priority on the top overall so sort puts D (pri 5) first, then A
    update_practice_score(
        mix_path=f"{base}/pj2026-a.wav",
        transition_index=0,
        priority=2,
        db_path=db,
    )

    items = list_best_practice_scores(
        prefix="pj", min_overall=7.0, saved_only=False, min_priority=0, db_path=db
    )
    names = [i["from_track"] for i in items]
    # other-mix excluded by prefix; C (6.0, unsaved, pri 0) excluded
    assert "X1" not in names
    assert "C1" not in names
    assert names[0] == "D1"  # priority 5 first
    assert names[1] == "A1"  # priority 2
    # B is save_for_set with overall 8, priority 0 — after prioritized
    assert "B1" in names
    assert items[0]["priority"] == 5
    assert items[0]["mix_name"] == "pj2026-d.wav"
    assert "overall" in items[0]
    assert "clip_start_sec" in items[0] or items[0].get("clip_start_sec") is None

    saved = list_best_practice_scores(
        prefix="pj", min_overall=7.0, saved_only=True, min_priority=0, db_path=db
    )
    assert [i["from_track"] for i in saved] == ["B1"]

    high_pri = list_best_practice_scores(
        prefix="pj", min_overall=7.0, saved_only=False, min_priority=3, db_path=db
    )
    assert [i["from_track"] for i in high_pri] == ["D1"]


def test_update_priority_and_save_for_set(tmp_path: Path):
    db = tmp_path / "upd.db"
    mix = "/Users/kirilllanger/Music/Mixes/pj2026-z.wav"
    save_practice_score(
        {
            "mix_path": mix,
            "from_track": "From",
            "to_track": "To",
            "transition_index": 2,
            "at_sec": 12.5,
            "overall": 7.2,
            "smoothness": 7.0,
            "creativity": 6.5,
            "flow": 7.1,
            "energy_match": 7.0,
            "comments": "nice blend",
            "save_for_set": 0,
            "analyzed_at": "2026-08-10T12:00:00",
            "clip_start_sec": 10.0,
            "clip_duration_sec": 45.0,
        },
        db_path=db,
    )
    row = update_practice_score(
        mix_path=mix, transition_index=2, priority=4, db_path=db
    )
    assert row["priority"] == 4
    assert row["save_for_set"] == 0
    assert row["overall"] == 7.2
    assert row["mix_name"] == "pj2026-z.wav"

    row2 = update_practice_score(id=row["id"], save_for_set=True, db_path=db)
    assert row2["priority"] == 4  # preserved
    assert row2["save_for_set"] == 1

    # Gemini re-save must not wipe manual priority
    save_practice_score(
        {
            "mix_path": mix,
            "from_track": "From",
            "to_track": "To",
            "transition_index": 2,
            "at_sec": 12.5,
            "overall": 7.8,
            "save_for_set": 1,
            "analyzed_at": "2026-08-11T12:00:00",
        },
        db_path=db,
    )
    items = list_best_practice_scores(prefix="pj", db_path=db)
    assert len(items) == 1
    assert items[0]["priority"] == 4
    assert items[0]["overall"] == 7.8
