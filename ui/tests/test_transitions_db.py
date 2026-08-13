"""Tests for transition notes import + lookup."""

from __future__ import annotations

from pathlib import Path

from sorter.transitions_db import (
    _parse_note_body,
    connect,
    import_history_csv,
    import_note_files,
    lookup_options,
    normalize_key,
    rebuild_database,
)


def test_normalize_key_basic():
    assert normalize_key("Cola") == "cola"
    assert "skin" in normalize_key("Dusky - Skin Deep (Original Mix)")


def test_parse_note_body_with_vibe(tmp_path: Path):
    body = """
Lord Feifer - My Face
# VIBE = GROOVIER
# keep drums
"""
    opts = _parse_note_body(body)
    assert len(opts) == 1
    assert "My Face" in opts[0]["to_raw"] or opts[0]["to_title"] == "My Face"
    assert "GROOVIER" in opts[0]["vibe"]


def test_parse_arrow_line():
    opts = _parse_note_body("Inflow - Old White -> PMAC - Body\n# dedede vocals\n")
    assert len(opts) == 1
    assert "Body" in opts[0]["to_raw"] or "Body" in opts[0]["to_title"]


def test_import_notes_and_lookup(tmp_path: Path):
    notes = tmp_path / "Transitions" / "Blvck Skyle" / "Nervous Girl"
    notes.parent.mkdir(parents=True)
    notes.write_text(
        "Kaiju - Lust # No Lust Vocals\n# VIBE = DARKER\n",
        encoding="utf-8",
    )
    db = tmp_path / "t.db"
    conn = connect(db)
    n = import_note_files(conn, roots=(tmp_path / "Transitions",))
    assert n >= 1
    conn.commit()
    conn.close()

    opts = lookup_options("Blvck Skyle - Nervous Girl", limit=5, db_path=db)
    assert opts
    labels = " ".join(o["to_label"] for o in opts).lower()
    assert "lust" in labels


def test_import_history_csv(tmp_path: Path):
    csv_path = tmp_path / "dj_transitions.csv"
    csv_path.write_text(
        'From Track,To Track,Count\n"Cola","Truth & Grace",3\n"Cola","Save Me",2\n',
        encoding="utf-8",
    )
    db = tmp_path / "h.db"
    conn = connect(db)
    n = import_history_csv(conn, csv_path=csv_path)
    assert n == 2
    conn.commit()
    conn.close()
    opts = lookup_options("Cola", limit=5, db_path=db)
    assert any("Truth" in o["to_label"] for o in opts)
