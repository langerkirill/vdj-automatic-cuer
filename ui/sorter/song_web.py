"""Short public-web blurbs for assemble evals (Wikipedia + iTunes)."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from .config import DJ_NOTES_ROOT

CACHE_PATH = DJ_NOTES_ROOT / "song_web_blurbs.json"
USER_AGENT = "MusicSorter/1.0 (local DJ playlist assemble)"
TIMEOUT_SEC = 5
MAX_BLURB = 280

_cache_lock = threading.Lock()
_mem: dict[str, dict[str, Any]] | None = None
_mem_path: str | None = None


def blurb_cache_key(artist: str = "", title: str = "", name: str = "") -> str:
    label = re.sub(r"\s+", " ", f"{artist} {title}".strip().lower())
    if label:
        return f"label:{label}"
    base = re.sub(r"^\d+[\s.\-]+", "", (name or "").strip().lower())
    base = re.sub(r"\.(m4a|mp3|flac|wav|aiff?)$", "", base)
    return f"name:{base}" if base else ""


def _read_cache() -> dict[str, dict[str, Any]]:
    global _mem, _mem_path
    with _cache_lock:
        current = str(CACHE_PATH)
        if _mem is not None and _mem_path == current:
            return _mem
        data: dict[str, dict[str, Any]] = {}
        if CACHE_PATH.is_file():
            try:
                raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
            except (OSError, json.JSONDecodeError):
                data = {}
        _mem = data
        _mem_path = current
        return data


def _write_cache(data: dict[str, dict[str, Any]]) -> None:
    global _mem, _mem_path
    with _cache_lock:
        _mem = data
        _mem_path = str(CACHE_PATH)
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = CACHE_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(CACHE_PATH)
        except OSError:
            pass


def _http_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
        raw = resp.read().decode("utf-8", "replace")
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _clip_blurb(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    joined = " ".join(parts[:2]).strip()
    if len(joined) > MAX_BLURB:
        joined = joined[: MAX_BLURB - 1].rsplit(" ", 1)[0] + "…"
    return joined


def wikipedia_blurb(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return ""
    search_url = (
        "https://en.wikipedia.org/w/api.php?"
        + urllib.parse.urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": q,
                "srlimit": 3,
                "format": "json",
            }
        )
    )
    search = _http_json(search_url)
    hits = ((search.get("query") or {}).get("search") or [])
    if not hits:
        return ""
    page_title = str(hits[0].get("title") or "")
    if not page_title:
        return ""
    extract_url = (
        "https://en.wikipedia.org/w/api.php?"
        + urllib.parse.urlencode(
            {
                "action": "query",
                "prop": "extracts",
                "exintro": 1,
                "explaintext": 1,
                "redirects": 1,
                "titles": page_title,
                "format": "json",
            }
        )
    )
    extracted = _http_json(extract_url)
    pages = ((extracted.get("query") or {}).get("pages") or {})
    for page in pages.values():
        if isinstance(page, dict) and page.get("extract"):
            return _clip_blurb(str(page["extract"]))
    return ""


def itunes_blurb(artist: str, title: str) -> str:
    term = f"{artist} {title}".strip()
    if not term:
        return ""
    url = (
        "https://itunes.apple.com/search?"
        + urllib.parse.urlencode({"term": term, "entity": "song", "limit": 1})
    )
    data = _http_json(url)
    results = data.get("results") or []
    if not results or not isinstance(results[0], dict):
        return ""
    hit = results[0]
    bits = [
        str(hit.get("primaryGenreName") or "").strip(),
        str(hit.get("collectionName") or "").strip(),
    ]
    bits = [b for b in bits if b]
    return " · ".join(bits)[:MAX_BLURB]


def lookup_song_blurb(
    *,
    artist: str = "",
    title: str = "",
    name: str = "",
    allow_network: bool = True,
) -> str:
    """Return a short public description, cached by artist+title."""
    key = blurb_cache_key(artist=artist, title=title, name=name)
    if not key:
        return ""
    cache = _read_cache()
    hit = cache.get(key)
    if hit and "blurb" in hit:
        return str(hit.get("blurb") or "")
    if not allow_network:
        return ""

    query = f"{artist} {title}".strip() or name
    blurb = ""
    try:
        song_q = f"{query} song"
        blurb = wikipedia_blurb(song_q)
        if not blurb and artist:
            blurb = wikipedia_blurb(artist)
        if not blurb:
            blurb = itunes_blurb(artist, title)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        blurb = ""

    next_cache = dict(cache)
    next_cache[key] = {
        "blurb": blurb,
        "artist": artist,
        "title": title,
        "updated_at": time.time(),
    }
    _write_cache(next_cache)
    return blurb


def lookup_blurbs_for_tracks(tracks: list[dict[str, Any]]) -> dict[str, str]:
    """Parallel blurbs keyed by track path."""
    out: dict[str, str] = {}
    if not tracks:
        return out

    def one(track: dict[str, Any]) -> tuple[str, str]:
        path = str(track.get("path") or "")
        blurb = lookup_song_blurb(
            artist=str(track.get("artist") or ""),
            title=str(track.get("title") or ""),
            name=str(track.get("name") or ""),
        )
        return path, blurb

    workers = min(4, len(tracks))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, t) for t in tracks]
        for fut in as_completed(futures):
            try:
                path, blurb = fut.result()
            except Exception:
                continue
            if path:
                out[path] = blurb
    return out
