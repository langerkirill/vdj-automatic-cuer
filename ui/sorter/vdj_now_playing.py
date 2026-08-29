"""Detect the track VirtualDJ is playing (History, LastPlay, or stale-history deck)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .config import AUDIO_EXTENSIONS, VDJ_CACHE_DB, VDJ_DATABASE, VDJ_HISTORY_DIR
from .relocate import summarize_cues, vdj_bpm_to_actual
from .musical_key import key_to_camelot, song_key_from_element, unescape_xml_text, vibe_label_from_path

_EXTVDJ_RE = re.compile(
    r"#EXTVDJ:(?P<meta>.*?)\n(?P<path>/[^\n]+)",
    re.DOTALL | re.IGNORECASE,
)
_LASTPLAY_RE = re.compile(r"<lastplaytime>(\d+)</lastplaytime>", re.I)
_ARTIST_RE = re.compile(r"<artist>(.*?)</artist>", re.I | re.DOTALL)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.I | re.DOTALL)
_DB_SONG_LASTPLAY_RE = re.compile(
    r'<Song\b(?P<attrs>[^>]*)>(?P<body>[\s\S]*?)</Song>',
    re.IGNORECASE,
)
_DB_FILEPATH_RE = re.compile(r'\bFilePath="([^"]+)"', re.IGNORECASE)
_DB_LASTPLAY_ATTR_RE = re.compile(r'\bLastPlay="(\d+)"', re.IGNORECASE)
_DB_TAGS_RE = re.compile(
    r'<Tags\b[^>]*\bAuthor="(?P<artist>[^"]*)"[^>]*\bTitle="(?P<title>[^"]*)"',
    re.IGNORECASE,
)

_lp_scan_cache: dict[str, Any] = {"mtime": None, "entry": None}
_deck_scan_cache: dict[str, Any] = {"mtime": None, "entry": None}

# History/LastPlay still wins while a set is in progress. Deck-load (waveform
# cache) is only now-playing when those play clocks are this far behind — the
# usual case is yesterday's History m3u while VDJ already has a track on deck.
DECK_STALE_SECONDS = 15 * 60


def best_lastplay_from_xml(text: str) -> Optional[tuple[int, str, str, str]]:
    """Return (lastplay, path, artist, title) for the newest LastPlay in XML."""
    best_lp = 0
    best_path = ""
    best_chunk = ""
    for m in _DB_SONG_LASTPLAY_RE.finditer(text):
        fp_m = _DB_FILEPATH_RE.search(m.group("attrs") or "")
        if not fp_m:
            continue
        lp_m = _DB_LASTPLAY_ATTR_RE.search(m.group("body") or "")
        if not lp_m:
            continue
        try:
            lp = int(lp_m.group(1))
        except ValueError:
            continue
        if lp >= best_lp:
            best_lp = lp
            best_path = unescape_xml_text(fp_m.group(1))
            best_chunk = m.group(0)
    if not best_path or not best_lp:
        return None
    artist, title = "", ""
    tm = _DB_TAGS_RE.search(best_chunk)
    if tm:
        artist = unescape_xml_text(tm.group("artist"))
        title = unescape_xml_text(tm.group("title"))
    if not title:
        title = Path(best_path).stem
    return (best_lp, best_path, artist, title)


@dataclass
class NowPlaying:
    path: str
    name: str
    artist: str
    title: str
    lastplay_unix: Optional[int]
    source: str  # history | database | deck
    bpm: Optional[float] = None
    key: str = ""
    camelot: str = ""
    is_cued: bool = False
    cue_count: int = 0
    genre: str = ""
    vibe: str = ""
    mix_windows: list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["mix_windows"] = list(self.mix_windows or [])
        return data


def _strip_tags(raw: str) -> str:
    return re.sub(r"<[^>]+>", "", raw or "").strip()


def _parse_history_text(text: str, *, file_mtime: int = 0) -> list[tuple[int, str, str, str]]:
    if text.startswith("\ufeff"):
        text = text[1:]
    entries: list[tuple[int, str, str, str]] = []
    for m in _EXTVDJ_RE.finditer(text):
        meta = m.group("meta")
        path = unescape_xml_text(m.group("path").strip())
        lp_m = _LASTPLAY_RE.search(meta)
        try:
            lp = int(lp_m.group(1)) if lp_m else 0
        except ValueError:
            lp = 0
        if not lp:
            lp = file_mtime
        artist = _strip_tags(_ARTIST_RE.search(meta).group(1) if _ARTIST_RE.search(meta) else "")
        title = _strip_tags(_TITLE_RE.search(meta).group(1) if _TITLE_RE.search(meta) else "")
        if Path(path).suffix.lower() not in AUDIO_EXTENSIONS and not path:
            continue
        entries.append((lp, path, artist, title))
    return entries


def _newest_history_file() -> Optional[Path]:
    root = VDJ_HISTORY_DIR
    if not root.is_dir():
        return None
    files = list(root.glob("*.m3u"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _waveform_cache_mtime() -> float:
    """cache.db / WAL mtime. Cheap; Recs stamp uses this to notice deck loads."""
    db = VDJ_CACHE_DB
    ts = 0.0
    try:
        if db.is_file():
            ts = float(db.stat().st_mtime)
        wal = db.with_name(db.name + "-wal")
        if wal.is_file():
            ts = max(ts, float(wal.stat().st_mtime))
    except OSError:
        pass
    return ts


def _waveforms_latest_row() -> Optional[tuple[str, str]]:
    """(filepath, filename) for the newest waveforms row, or None.

    VDJ keeps cache.db locked while open, so a live sqlite connection times
    out. Snapshot db+WAL instead; caller caches by WAL mtime.
    """
    db = VDJ_CACHE_DB
    if not db.is_file():
        return None
    import sqlite3
    import shutil
    import tempfile

    try:
        with tempfile.TemporaryDirectory(prefix="vdj-cache-") as td:
            snap = Path(td) / "cache.db"
            shutil.copy2(db, snap)
            wal = db.with_name(db.name + "-wal")
            shm = db.with_name(db.name + "-shm")
            if wal.is_file():
                shutil.copy2(wal, Path(td) / "cache.db-wal")
            if shm.is_file():
                shutil.copy2(shm, Path(td) / "cache.db-shm")
            con = sqlite3.connect(str(snap), timeout=0.25)
            try:
                cur = con.execute(
                    "SELECT filepath, filename FROM waveforms "
                    "ORDER BY id DESC LIMIT 1"
                )
                row = cur.fetchone()
            finally:
                con.close()
        if row:
            return (row[0] or "", row[1] or "")
    except Exception:
        return None
    return None


def latest_deck_waveform() -> Optional[tuple[int, str, str, str]]:
    """
    Most recently loaded deck file from VDJ waveform cache.

    VDJ writes this when a track is loaded on a deck (before History logs a play).
    """
    ts = _waveform_cache_mtime()
    if _deck_scan_cache["mtime"] == ts:
        return _deck_scan_cache["entry"]
    row = _waveforms_latest_row()
    if not row:
        _deck_scan_cache["mtime"] = ts
        _deck_scan_cache["entry"] = None
        return None
    folder, name = row
    path = str(Path(folder) / name) if folder else name
    if not path:
        _deck_scan_cache["mtime"] = ts
        _deck_scan_cache["entry"] = None
        return None
    entry = (int(ts), path, "", Path(path).stem)
    _deck_scan_cache["mtime"] = ts
    _deck_scan_cache["entry"] = entry
    return entry


def latest_database_play(*, force: bool = False) -> Optional[tuple[int, str, str, str]]:
    """
    Song with the newest Infos LastPlay in database.xml.

    LastPlay updates when a track is actually played, not when it is only
    loaded onto a deck. Cached by database mtime so Refresh stays fast.
    """
    db = VDJ_DATABASE
    if not db.is_file():
        return None
    try:
        mtime = db.stat().st_mtime
    except OSError:
        return None
    if (
        not force
        and _lp_scan_cache["mtime"] == mtime
        and _lp_scan_cache["entry"] is not None
    ):
        return _lp_scan_cache["entry"]

    try:
        from vdj_database_safety import read_vdj_database_text

        text = read_vdj_database_text(db)
    except Exception:
        try:
            text = db.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    entry = best_lastplay_from_xml(text)
    if entry is None:
        _lp_scan_cache["mtime"] = mtime
        _lp_scan_cache["entry"] = None
        return None
    _lp_scan_cache["mtime"] = mtime
    _lp_scan_cache["entry"] = entry
    return entry


def latest_history_entry() -> Optional[tuple[int, str, str, str]]:
    """
    Last track VDJ wrote to History (newest file, last block).

    This is faster and more current than max(lastplay) across old playlists.
    """
    newest = _newest_history_file()
    if newest is None:
        return None
    try:
        text = newest.read_text(encoding="utf-8", errors="replace")
        mtime = int(newest.stat().st_mtime)
    except OSError:
        return None
    entries = _parse_history_text(text, file_mtime=mtime)
    if not entries:
        return None
    # Last line in today's file is what VDJ just appended
    return entries[-1]


def _history_files_for_date(day: date) -> list[Path]:
    """Dated History m3u paths VDJ uses (root + nested year/month)."""
    root = VDJ_HISTORY_DIR
    if not root.is_dir():
        return []
    candidates = (
        root / f"{day.isoformat()}.m3u",
        root / f"{day.year}" / f"{day:%m-%d}.m3u",
        root / f"{day.year}" / f"{day:%m}" / f"{day.isoformat()}.m3u",
        root / f"{day.year}" / f"{day:%m}" / f"{day:%m-%d}.m3u",
    )
    files: list[Path] = []
    for path in candidates:
        if path.is_file() and path not in files:
            files.append(path)
    return files


def history_plays_on_dates(
    dates: set[date] | frozenset[date],
) -> list[tuple[int, str, str, str]]:
    """VDJ History plays whose file date or lastplay falls on ``dates``."""
    wanted = set(dates or ())
    if not wanted:
        return []
    root = VDJ_HISTORY_DIR
    if not root.is_dir():
        return []
    files: list[Path] = []
    dated_files: set[Path] = set()
    for day in wanted:
        for path in _history_files_for_date(day):
            if path not in files:
                files.append(path)
            dated_files.add(path)
    newest = _newest_history_file()
    if newest is not None and newest not in files:
        files.append(newest)

    seen: set[tuple[str, str, str]] = set()
    out: list[tuple[int, str, str, str]] = []
    for m3u in files:
        try:
            text = m3u.read_text(encoding="utf-8", errors="replace")
            mtime = int(m3u.stat().st_mtime)
        except OSError:
            continue
        dated_file = m3u in dated_files
        for lp, path, artist, title in _parse_history_text(text, file_mtime=mtime):
            if dated_file:
                keep = True
            elif lp:
                try:
                    keep = datetime.fromtimestamp(lp).date() in wanted
                except (OverflowError, OSError, ValueError):
                    keep = False
            else:
                keep = False
            if not keep:
                continue
            stamp = (path.lower(), (artist or "").lower(), (title or "").lower())
            if stamp in seen:
                continue
            seen.add(stamp)
            out.append((lp, path, artist, title))
    return out


def todays_history_plays() -> list[tuple[int, str, str, str]]:
    """VDJ History plays from today (local date). Path + artist/title."""
    return history_plays_on_dates({datetime.now().date()})


def recent_history_play_groups(
    *,
    days: int = 3,
    today: date | None = None,
) -> dict[str, list[tuple[int, str, str, str]]]:
    """Split recent History plays into today / yesterday / earlier-in-window.

    ``days`` is a rolling calendar window ending today (3 = Fri–Sat event plus
    the day before). Each play is listed once; today wins, then yesterday.
    """
    window = max(1, int(days))
    today_d = today or datetime.now().date()
    yesterday = today_d - timedelta(days=1)
    all_dates = {today_d - timedelta(days=i) for i in range(window)}
    earlier_dates = all_dates - {today_d, yesterday}

    today_plays = history_plays_on_dates({today_d})
    yesterday_plays = history_plays_on_dates({yesterday}) if window >= 2 else []
    earlier_plays = history_plays_on_dates(earlier_dates) if earlier_dates else []

    today_stamps = {
        (p[1].lower(), (p[2] or "").lower(), (p[3] or "").lower())
        for p in today_plays
    }
    yesterday_only = [
        p
        for p in yesterday_plays
        if (p[1].lower(), (p[2] or "").lower(), (p[3] or "").lower())
        not in today_stamps
    ]
    yest_stamps = {
        (p[1].lower(), (p[2] or "").lower(), (p[3] or "").lower())
        for p in yesterday_only
    } | today_stamps
    earlier_only = [
        p
        for p in earlier_plays
        if (p[1].lower(), (p[2] or "").lower(), (p[3] or "").lower())
        not in yest_stamps
    ]
    all_plays = today_plays + yesterday_only + earlier_only
    return {
        "today": today_plays,
        "yesterday": yesterday_only,
        "earlier": earlier_only,
        "all": all_plays,
    }


def _history_entries() -> list[tuple[int, str, str, str]]:
    """Return (lastplay_unix, path, artist, title) from VDJ History m3u files."""
    root = VDJ_HISTORY_DIR
    if not root.is_dir():
        return []
    entries: list[tuple[int, str, str, str]] = []
    files = sorted(root.glob("*.m3u"), key=lambda p: p.stat().st_mtime, reverse=True)
    for m3u in files[:8]:
        try:
            text = m3u.read_text(encoding="utf-8", errors="replace")
            mtime = int(m3u.stat().st_mtime)
        except OSError:
            continue
        entries.extend(_parse_history_text(text, file_mtime=mtime))
    return entries


def _load_song_element(audio_path: str):
    """Load VDJ Song element or None."""
    db = VDJ_DATABASE
    if not db.is_file():
        return None
    try:
        from vdj_database_safety import load_song_element, normalize_database_path

        return load_song_element(db, normalize_database_path(audio_path))
    except Exception:
        try:
            from vdj_database_safety import load_song_element

            return load_song_element(db, str(Path(audio_path).expanduser()))
        except Exception:
            return None


def _song_key_from_database(audio_path: str) -> str:
    """Best-effort key lookup: Tags.Key / Tags.Harmonic, then Scan.Key (VDJ analysis)."""
    return song_key_from_element(_load_song_element(audio_path))


def _song_genre_and_vibe(audio_path: str) -> tuple[str, str]:
    """Return (genre, vibe) from Tags.Genre + folder path under known libraries."""
    genre = ""
    song = _load_song_element(audio_path)
    if song is not None:
        tags = song.find("Tags")
        if tags is not None:
            genre = unescape_xml_text(tags.get("Genre") or "")
    vibe = ""
    try:
        from .config import CUES_SORTED, LIBRARIES, READY_FOR_SORT

        p = Path(audio_path).expanduser()
        roots = [
            ("House", LIBRARIES.get("House")),
            ("Zouk", LIBRARIES.get("Zouk")),
            ("Cues Sorted", CUES_SORTED),
            ("Ready for Sort", READY_FOR_SORT),
        ]
        for lib_name, root in roots:
            if root is None:
                continue
            try:
                rel = p.resolve().relative_to(Path(root).resolve())
                vibe = vibe_label_from_path(str(rel), lib_name)
                if vibe:
                    break
            except (ValueError, OSError):
                continue
        if not vibe:
            # Fallback: last 2 parent folder names
            parts = list(p.parts)
            if len(parts) >= 3:
                vibe = " / ".join(parts[-3:-1])
    except Exception:
        vibe = ""
    return genre, vibe


def _same_track_path(left: str, right: str) -> bool:
    a = (left or "").strip().lower()
    b = (right or "").strip().lower()
    return bool(a) and a == b


def _deck_is_ahead(
    play: Optional[tuple[int, str, str, str]],
    deck: Optional[tuple[int, str, str, str]],
) -> bool:
    """True when deck-load is the only current signal (play clocks went stale)."""
    if not deck:
        return False
    if not play:
        return True
    if _same_track_path(play[1], deck[1]):
        return False
    play_ts = int(play[0] or 0)
    deck_ts = int(deck[0] or 0)
    if deck_ts <= play_ts:
        return False
    return (deck_ts - play_ts) > DECK_STALE_SECONDS


def pick_now_playing(
    hist: Optional[tuple[int, str, str, str]],
    db_play: Optional[tuple[int, str, str, str]],
    deck: Optional[tuple[int, str, str, str]] = None,
) -> tuple[Optional[tuple[int, str, str, str]], str]:
    """
    Choose History last line vs database LastPlay.

    A deck-load (waveform cache) wins only when those play clocks are stale —
    loading the next track during a live set must not steal Recs.
    """
    if hist and db_play:
        if db_play[0] > hist[0]:
            picked, source = db_play, "database"
        else:
            picked, source = hist, "history"
    elif db_play:
        picked, source = db_play, "database"
    elif hist:
        picked, source = hist, "history"
    else:
        picked, source = None, "history"
    if _deck_is_ahead(picked, deck):
        return deck, "deck"
    return picked, source


def get_now_playing(
    *,
    enrich: bool = True,
    prefer_latest_file: bool = True,
    force_rescan: bool = False,
) -> Optional[NowPlaying]:
    """
    Track VirtualDJ is actually playing.

    Prefers History plus database LastPlay. Falls back to the latest deck-load
    when those play clocks are stale (no History file for today, LastPlay from
    yesterday, etc.). A song only loaded onto the other deck during a live set
    is ignored.
    """
    hist = latest_history_entry() if prefer_latest_file else None
    db_play = latest_database_play(force=force_rescan)
    deck = latest_deck_waveform()
    picked, source = pick_now_playing(hist, db_play, deck)
    if picked is None:
        entries = _history_entries()
        if not entries:
            return None
        entries.sort(key=lambda e: e[0], reverse=True)
        picked = entries[0]
        source = "history"
    lp, path, artist, title = picked
    name = Path(path).name
    if not title and not artist:
        title = Path(path).stem
    np = NowPlaying(
        path=path,
        name=name,
        artist=artist,
        title=title,
        lastplay_unix=lp or None,
        source=source,
    )
    if not enrich:
        return np

    cues = summarize_cues(path)
    np.bpm = cues.bpm
    np.is_cued = cues.is_cued
    np.cue_count = cues.cue_count
    if cues.title and not np.title:
        np.title = cues.title
    if cues.author and not np.artist:
        np.artist = cues.author
    key = _song_key_from_database(path)
    np.key = key
    np.camelot = key_to_camelot(key) or ""
    genre, vibe = _song_genre_and_vibe(path)
    np.genre = genre
    np.vibe = vibe
    try:
        from .transition_timing import markers_from_cue_summary, mix_out_windows

        markers, length = markers_from_cue_summary(cues)
        np.mix_windows = mix_out_windows(markers, song_length=length)
    except Exception:
        np.mix_windows = []
    return np


def now_playing_stamp() -> dict[str, Any]:
    """Cheap history/db/cache mtime + last path. No enrich, no database XML scan."""
    newest = _newest_history_file()
    hist_mtime = 0.0
    if newest is not None:
        try:
            hist_mtime = float(newest.stat().st_mtime)
        except OSError:
            hist_mtime = 0.0
    db_mtime = 0.0
    if VDJ_DATABASE.is_file():
        try:
            db_mtime = float(VDJ_DATABASE.stat().st_mtime)
        except OSError:
            db_mtime = 0.0
    hist = latest_history_entry()
    cache_mtime = _waveform_cache_mtime()
    deck = None
    if _deck_is_ahead(hist, (int(cache_mtime), "", "", "")):
        deck = latest_deck_waveform()
    picked, source = pick_now_playing(hist, None, deck)
    play_mtime = max(hist_mtime, db_mtime)
    if picked is None:
        return {
            "path": "",
            "lastplay": 0,
            "mtime": max(play_mtime, cache_mtime),
            "source": source,
            "artist": "",
            "title": "",
        }
    lp, path, artist, title = picked
    # Deck fingerprint is the path. Do not put cache WAL / database.xml mtime
    # in lastplay or mtime or Recs would re-enrich on every VDJ write.
    deck = source == "deck"
    return {
        "path": path,
        "lastplay": 0 if deck else int(lp or 0),
        "mtime": 0 if deck else play_mtime,
        "source": source,
        "artist": artist,
        "title": title,
    }


def format_lastplay(unix_ts: Optional[int]) -> str:
    if not unix_ts:
        return ""
    try:
        return datetime.fromtimestamp(unix_ts).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return str(unix_ts)
