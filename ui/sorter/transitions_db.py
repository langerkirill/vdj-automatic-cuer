"""SQLite database of DJ transition notes + play-history edges.

Sources:
  - Freeform note files under Music/DJ/Notes/{,Zouk/,House/}Transitions/Artist/Song
  - VirtualDJ History/dj_transitions.csv (From Track, To Track, Count)
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Optional

from .config import (
    DJ_TRANSITIONS_CSV,
    TRANSITION_NOTES_DIRS,
    TRANSITIONS_DB_PATH,
)

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_artist TEXT NOT NULL DEFAULT '',
  from_title TEXT NOT NULL DEFAULT '',
  from_key TEXT NOT NULL,
  to_raw TEXT NOT NULL,
  to_artist TEXT NOT NULL DEFAULT '',
  to_title TEXT NOT NULL DEFAULT '',
  to_key TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  vibe TEXT NOT NULL DEFAULT '',
  source_path TEXT NOT NULL,
  genre TEXT NOT NULL DEFAULT '',
  UNIQUE(source_path, to_raw)
);

CREATE INDEX IF NOT EXISTS idx_note_from ON note_edges(from_key);
CREATE INDEX IF NOT EXISTS idx_note_to ON note_edges(to_key);

CREATE TABLE IF NOT EXISTS history_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_track TEXT NOT NULL,
  to_track TEXT NOT NULL,
  from_key TEXT NOT NULL,
  to_key TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 1,
  UNIQUE(from_track, to_track)
);

CREATE INDEX IF NOT EXISTS idx_hist_from ON history_edges(from_key);
CREATE INDEX IF NOT EXISTS idx_hist_to ON history_edges(to_key);

CREATE TABLE IF NOT EXISTS practice_scores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mix_path TEXT NOT NULL,
  from_track TEXT NOT NULL,
  to_track TEXT NOT NULL,
  transition_index INTEGER NOT NULL,
  at_sec REAL NOT NULL DEFAULT 0,
  overall REAL,
  smoothness REAL,
  creativity REAL,
  flow REAL,
  energy_match REAL,
  comments TEXT NOT NULL DEFAULT '',
  strengths TEXT NOT NULL DEFAULT '[]',
  improvements TEXT NOT NULL DEFAULT '[]',
  save_for_set INTEGER NOT NULL DEFAULT 0,
  model TEXT NOT NULL DEFAULT '',
  analyzed_at TEXT NOT NULL,
  UNIQUE(mix_path, transition_index)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_key(text: str) -> str:
    """Loose key for matching titles across notes / history / practice sets."""
    s = (text or "").lower()
    s = s.replace("&", " and ")
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"\([^)]*\)", " ", s)  # drop (remix) etc for matching
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _split_artist_title(raw: str) -> tuple[str, str]:
    line = (raw or "").strip()
    if not line:
        return "", ""
    # Strip trailing # comments already handled by caller
    for sep in (" - ", " – ", " — ", " -> ", " → "):
        if sep in line:
            a, b = line.split(sep, 1)
            return a.strip(), b.strip()
    return "", line


def _parse_note_body(body: str) -> list[dict[str, str]]:
    """Parse note file body into destination options with optional comments/vibe."""
    options: list[dict[str, str]] = []
    pending_to: Optional[dict[str, str]] = None
    vibe = ""
    note_lines: list[str] = []

    def flush() -> None:
        nonlocal pending_to, vibe, note_lines
        if not pending_to:
            vibe = ""
            note_lines = []
            return
        pending_to["vibe"] = vibe.strip()
        pending_to["note"] = "\n".join(note_lines).strip()
        options.append(pending_to)
        pending_to = None
        vibe = ""
        note_lines = []

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            content = line.lstrip("#").strip()
            vibe_m = re.match(r"VIBE\s*=\s*(.+)", content, re.I)
            if vibe_m:
                vibe = vibe_m.group(1).strip()
            else:
                note_lines.append(content)
            continue

        # New destination line (may include inline # comment)
        flush()
        main, _, inline = line.partition("#")
        main = main.strip()
        # Patterns: "A - B -> C - D"  or "A - B"
        if "->" in main or "→" in main:
            left, right = re.split(r"->|→", main, maxsplit=1)
            # Prefer right-hand side as destination when arrow present
            dest = right.strip() or left.strip()
        else:
            dest = main
        to_artist, to_title = _split_artist_title(dest)
        pending_to = {
            "to_raw": dest,
            "to_artist": to_artist,
            "to_title": to_title or dest,
        }
        if inline.strip():
            note_lines.append(inline.strip())

    flush()
    return options


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or TRANSITIONS_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def import_note_files(
    conn: sqlite3.Connection,
    roots: tuple[Path, ...] | None = None,
) -> int:
    roots = roots or TRANSITION_NOTES_DIRS
    seen_paths: set[str] = set()
    count = 0
    conn.execute("DELETE FROM note_edges")
    for root in roots:
        if not root.is_dir():
            continue
        genre = ""
        name = root.name.lower()
        parent = root.parent.name.lower()
        if "house" in parent or name == "house":
            genre = "House"
        elif "zouk" in parent or name == "zouk":
            genre = "Zouk"
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            # Skip junk
            if path.name.startswith(".") or path.suffix.lower() in {
                ".ds_store",
                ".pyc",
            }:
                continue
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            parts = rel.parts
            if len(parts) < 2:
                continue
            from_artist = parts[0]
            from_title = parts[1]
            # If deeper nesting, treat last as title
            if len(parts) > 2:
                from_title = "/".join(parts[1:])
            sp = str(path.resolve())
            if sp in seen_paths:
                continue
            seen_paths.add(sp)
            try:
                body = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            from_key = normalize_key(f"{from_artist} {from_title}")
            options = _parse_note_body(body)
            if not options:
                # Empty note still records the from track with blank destination
                continue
            for opt in options:
                to_key = normalize_key(
                    f"{opt.get('to_artist','')} {opt.get('to_title','')}"
                    if opt.get("to_artist")
                    else opt.get("to_raw", "")
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO note_edges
                    (from_artist, from_title, from_key, to_raw, to_artist, to_title,
                     to_key, note, vibe, source_path, genre)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        from_artist,
                        from_title,
                        from_key,
                        opt["to_raw"],
                        opt.get("to_artist") or "",
                        opt.get("to_title") or "",
                        to_key,
                        opt.get("note") or "",
                        opt.get("vibe") or "",
                        sp,
                        genre,
                    ),
                )
                count += 1
    return count


def import_history_csv(
    conn: sqlite3.Connection,
    csv_path: Path | None = None,
) -> int:
    path = Path(csv_path or DJ_TRANSITIONS_CSV)
    conn.execute("DELETE FROM history_edges")
    if not path.is_file():
        return 0
    count = 0
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            frm = (row.get("From Track") or row.get("from") or "").strip()
            to = (row.get("To Track") or row.get("to") or "").strip()
            if not frm or not to:
                continue
            try:
                n = int(float(row.get("Count") or row.get("count") or 1))
            except (TypeError, ValueError):
                n = 1
            conn.execute(
                """
                INSERT OR REPLACE INTO history_edges
                (from_track, to_track, from_key, to_key, count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (frm, to, normalize_key(frm), normalize_key(to), n),
            )
            count += 1
    return count


def rebuild_database(db_path: Path | None = None) -> dict[str, Any]:
    """Full reimport of notes + history CSV into SQLite."""
    with _lock:
        conn = connect(db_path)
        try:
            notes_n = import_note_files(conn)
            hist_n = import_history_csv(conn)
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                ("rebuilt_at", _now_iso()),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                ("note_edges", str(notes_n)),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                ("history_edges", str(hist_n)),
            )
            conn.commit()
            stats = get_stats(conn)
            stats["rebuilt_at"] = _now_iso()
            stats["imported_notes"] = notes_n
            stats["imported_history"] = hist_n
            return stats
        finally:
            conn.close()


def get_stats(conn: Optional[sqlite3.Connection] = None) -> dict[str, Any]:
    own = conn is None
    if own:
        conn = connect()
    try:
        notes = conn.execute("SELECT COUNT(*) FROM note_edges").fetchone()[0]
        hist = conn.execute("SELECT COUNT(*) FROM history_edges").fetchone()[0]
        scores = conn.execute("SELECT COUNT(*) FROM practice_scores").fetchone()[0]
        rebuilt = conn.execute(
            "SELECT value FROM meta WHERE key='rebuilt_at'"
        ).fetchone()
        return {
            "db_path": str(TRANSITIONS_DB_PATH),
            "note_edges": notes,
            "history_edges": hist,
            "practice_scores": scores,
            "rebuilt_at": rebuilt[0] if rebuilt else None,
        }
    finally:
        if own:
            conn.close()


def ensure_database() -> dict[str, Any]:
    """Create + import if empty / missing."""
    path = TRANSITIONS_DB_PATH
    if not path.is_file():
        return rebuild_database()
    conn = connect()
    try:
        notes = conn.execute("SELECT COUNT(*) FROM note_edges").fetchone()[0]
        hist = conn.execute("SELECT COUNT(*) FROM history_edges").fetchone()[0]
        if notes == 0 and hist == 0:
            return rebuild_database()
        return get_stats(conn)
    finally:
        conn.close()


@dataclass
class TransitionOption:
    source: str  # note | history
    to_label: str
    to_artist: str = ""
    to_title: str = ""
    count: int = 0
    note: str = ""
    vibe: str = ""
    genre: str = ""
    score: float = 0.0  # ranking weight for UI

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _token_overlap(a: str, b: str) -> float:
    ta = set(normalize_key(a).split())
    tb = set(normalize_key(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def lookup_options(
    from_track: str,
    *,
    limit: int = 12,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Find alternate / known transitions out of a track from notes + history."""
    ensure_database()
    key = normalize_key(from_track)
    if not key:
        return []
    tokens = key.split()
    conn = connect(db_path)
    try:
        options: list[TransitionOption] = []

        # Exact-ish key match on notes
        rows = conn.execute(
            """
            SELECT * FROM note_edges
            WHERE from_key = ?
               OR from_key LIKE ?
            ORDER BY id
            """,
            (key, f"%{key}%"),
        ).fetchall()
        # Also fuzzy: any from_key sharing enough tokens
        if len(rows) < 3 and tokens:
            like_clauses = " OR ".join(["from_key LIKE ?" for _ in tokens[:4]])
            params = [f"%{t}%" for t in tokens[:4]]
            rows2 = conn.execute(
                f"SELECT * FROM note_edges WHERE {like_clauses} LIMIT 80",
                params,
            ).fetchall()
            rows = list({r["id"]: r for r in list(rows) + list(rows2)}.values())

        for r in rows:
            ov = _token_overlap(from_track, f"{r['from_artist']} {r['from_title']}")
            if ov < 0.35 and r["from_key"] != key:
                continue
            label = r["to_raw"] or f"{r['to_artist']} - {r['to_title']}".strip(" -")
            options.append(
                TransitionOption(
                    source="note",
                    to_label=label,
                    to_artist=r["to_artist"] or "",
                    to_title=r["to_title"] or "",
                    note=r["note"] or "",
                    vibe=r["vibe"] or "",
                    genre=r["genre"] or "",
                    score=10.0 + ov * 5 + (2.0 if r["vibe"] else 0) + (1.0 if r["note"] else 0),
                )
            )

        hrows = conn.execute(
            """
            SELECT * FROM history_edges
            WHERE from_key = ? OR from_key LIKE ?
            ORDER BY count DESC
            LIMIT 40
            """,
            (key, f"%{key}%"),
        ).fetchall()
        if len(hrows) < 3 and tokens:
            like_clauses = " OR ".join(["from_key LIKE ?" for _ in tokens[:4]])
            params = [f"%{t}%" for t in tokens[:4]]
            hrows2 = conn.execute(
                f"SELECT * FROM history_edges WHERE {like_clauses} "
                f"ORDER BY count DESC LIMIT 60",
                params,
            ).fetchall()
            hrows = list({(r["from_track"], r["to_track"]): r for r in list(hrows) + list(hrows2)}.values())

        for r in hrows:
            ov = _token_overlap(from_track, r["from_track"])
            if ov < 0.35 and r["from_key"] != key:
                continue
            options.append(
                TransitionOption(
                    source="history",
                    to_label=r["to_track"],
                    count=int(r["count"] or 1),
                    score=float(r["count"] or 1) + ov * 3,
                )
            )

        # Dedupe by normalized to_label, keep highest score
        best: dict[str, TransitionOption] = {}
        for opt in options:
            k = normalize_key(opt.to_label)
            if not k:
                continue
            prev = best.get(k)
            if prev is None or opt.score > prev.score:
                # merge note text if history overwrites
                if prev and prev.source == "note" and opt.source == "history":
                    opt.note = prev.note or opt.note
                    opt.vibe = prev.vibe or opt.vibe
                    opt.source = "note+history"
                    opt.score = max(opt.score, prev.score) + 1
                best[k] = opt

        ranked = sorted(best.values(), key=lambda o: o.score, reverse=True)
        return [o.to_dict() for o in ranked[:limit]]
    finally:
        conn.close()


def _ensure_score_columns(conn: sqlite3.Connection) -> None:
    cols = {
        r[1]
        for r in conn.execute("PRAGMA table_info(practice_scores)").fetchall()
    }
    alters = {
        "strengths": "TEXT NOT NULL DEFAULT '[]'",
        "improvements": "TEXT NOT NULL DEFAULT '[]'",
        "better_option_track": "TEXT NOT NULL DEFAULT ''",
        "better_option_reason": "TEXT NOT NULL DEFAULT ''",
        "better_option_source": "TEXT NOT NULL DEFAULT ''",
        "better_option_confidence": "REAL",
        "clip_start_sec": "REAL",
        "clip_duration_sec": "REAL",
        "priority": "INTEGER NOT NULL DEFAULT 0",
    }
    for col, decl in alters.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE practice_scores ADD COLUMN {col} {decl}")


def save_practice_score(record: dict[str, Any], db_path: Path | None = None) -> None:
    conn = connect(db_path)
    try:
        _ensure_score_columns(conn)
        mix_path = record["mix_path"]
        transition_index = int(record["transition_index"])
        existing = conn.execute(
            """
            SELECT priority FROM practice_scores
            WHERE mix_path = ? AND transition_index = ?
            """,
            (mix_path, transition_index),
        ).fetchone()
        if record.get("priority") is not None:
            priority = int(record["priority"])
        elif existing is not None:
            priority = int(existing["priority"] or 0)
        else:
            priority = 0
        conn.execute(
            """
            INSERT OR REPLACE INTO practice_scores
            (mix_path, from_track, to_track, transition_index, at_sec,
             overall, smoothness, creativity, flow, energy_match,
             comments, strengths, improvements, save_for_set, model, analyzed_at,
             better_option_track, better_option_reason, better_option_source,
             better_option_confidence, clip_start_sec, clip_duration_sec, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mix_path,
                record["from_track"],
                record["to_track"],
                transition_index,
                float(record.get("at_sec") or 0),
                record.get("overall"),
                record.get("smoothness"),
                record.get("creativity"),
                record.get("flow"),
                record.get("energy_match"),
                record.get("comments") or "",
                json.dumps(record.get("strengths") or []),
                json.dumps(record.get("improvements") or []),
                1 if record.get("save_for_set") else 0,
                record.get("model") or "",
                record.get("analyzed_at") or _now_iso(),
                record.get("better_option_track") or "",
                record.get("better_option_reason") or "",
                record.get("better_option_source") or "",
                record.get("better_option_confidence"),
                record.get("clip_start_sec"),
                record.get("clip_duration_sec"),
                priority,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def load_practice_scores(
    mix_path: str, db_path: Path | None = None
) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        _ensure_score_columns(conn)
        rows = conn.execute(
            """
            SELECT * FROM practice_scores
            WHERE mix_path = ?
            ORDER BY transition_index
            """,
            (mix_path,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for key in ("strengths", "improvements"):
                raw = d.get(key) or "[]"
                if isinstance(raw, str):
                    try:
                        d[key] = json.loads(raw)
                    except json.JSONDecodeError:
                        d[key] = []
            out.append(d)
        return out
    finally:
        conn.close()


def _row_to_score_dict(r: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    d = dict(r)
    for key in ("strengths", "improvements"):
        raw = d.get(key) or "[]"
        if isinstance(raw, str):
            try:
                d[key] = json.loads(raw)
            except json.JSONDecodeError:
                d[key] = []
    d["save_for_set"] = 1 if d.get("save_for_set") else 0
    d["priority"] = int(d.get("priority") or 0)
    mix_path = d.get("mix_path") or ""
    d["mix_name"] = Path(mix_path).name if mix_path else ""
    return d


def list_best_practice_scores(
    prefix: str = "pj",
    min_overall: float = 7.0,
    saved_only: bool = False,
    min_priority: int = 0,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Cross-mix shortlist ordered by user priority, then Gemini rankings.

    Default inclusion (when saved_only is False):
      save_for_set=1 OR overall >= min_overall OR priority >= 1
    Always also requires priority >= min_priority (0 = no floor).
    Filename filter: mix basename starts with ``prefix`` (case-insensitive).
    """
    conn = connect(db_path)
    try:
        _ensure_score_columns(conn)
        rows = conn.execute("SELECT * FROM practice_scores").fetchall()
        prefix_l = (prefix or "").lower()
        min_overall_f = float(min_overall)
        min_priority_i = int(min_priority or 0)
        out: list[dict[str, Any]] = []
        for r in rows:
            d = _row_to_score_dict(r)
            name = d["mix_name"]
            if prefix_l and not name.lower().startswith(prefix_l):
                continue
            priority = int(d.get("priority") or 0)
            if priority < min_priority_i:
                continue
            saved = bool(d.get("save_for_set"))
            overall = d.get("overall")
            overall_n = float(overall) if overall is not None else None
            if saved_only:
                if not saved:
                    continue
            else:
                if not (
                    saved
                    or (overall_n is not None and overall_n >= min_overall_f)
                    or priority >= 1
                ):
                    continue
            out.append(d)

        # Stable multi-pass: priority DESC, overall DESC, save_for_set DESC,
        # analyzed_at DESC, mix_name ASC.
        out.sort(key=lambda i: (i.get("mix_name") or ""))
        out.sort(key=lambda i: (i.get("analyzed_at") or ""), reverse=True)
        out.sort(key=lambda i: (1 if i.get("save_for_set") else 0), reverse=True)
        out.sort(
            key=lambda i: (
                float(i["overall"]) if i.get("overall") is not None else -1.0
            ),
            reverse=True,
        )
        out.sort(key=lambda i: int(i.get("priority") or 0), reverse=True)
        return out
    finally:
        conn.close()


def update_practice_score(
    *,
    id: int | None = None,
    mix_path: str | None = None,
    transition_index: int | None = None,
    priority: int | None = None,
    save_for_set: bool | int | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Patch priority and/or save_for_set on one practice_scores row."""
    if priority is None and save_for_set is None:
        raise ValueError("Provide priority and/or save_for_set to update")
    if id is None and (not mix_path or transition_index is None):
        raise ValueError("Provide id or mix_path+transition_index")

    conn = connect(db_path)
    try:
        _ensure_score_columns(conn)
        if id is not None:
            row = conn.execute(
                "SELECT * FROM practice_scores WHERE id = ?",
                (int(id),),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT * FROM practice_scores
                WHERE mix_path = ? AND transition_index = ?
                """,
                (mix_path, int(transition_index)),
            ).fetchone()
        if row is None:
            raise KeyError("practice_scores row not found")

        sets: list[str] = []
        params: list[Any] = []
        if priority is not None:
            pr = int(priority)
            if pr < 0 or pr > 5:
                raise ValueError("priority must be 0–5")
            sets.append("priority = ?")
            params.append(pr)
        if save_for_set is not None:
            sets.append("save_for_set = ?")
            params.append(1 if save_for_set else 0)
        params.append(int(row["id"]))
        conn.execute(
            f"UPDATE practice_scores SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM practice_scores WHERE id = ?",
            (int(row["id"]),),
        ).fetchone()
        return _row_to_score_dict(updated)
    finally:
        conn.close()
