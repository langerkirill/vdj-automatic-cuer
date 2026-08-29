"""Assemble mix / ranking policy. Pure — no Gemini, disk, or database.xml."""

from __future__ import annotations

from typing import Any, Optional

from .musical_key import genre_family

DEFAULT_CHUNK = 12
DEFAULT_TARGET = 400
MIN_TARGET = 300
MAX_TARGET = 500
MIN_CHUNK = 10
MAX_CHUNK = 20
DEFAULT_MIN_FIT = 0.60

LANE_SHARE = {
    "chill": 0.24,
    "energy": 0.14,
    "rnb": 0.12,
    "kizouk": 0.10,
    "lamba": 0.08,
    "trancy": 0.05,
    "hiphop": 0.04,
    "remixes": 0.06,
    "classics": 0.05,
    "nostalgia": 0.04,
    "tribal": 0.0,
    "bassy": 0.0,
    "experimental": 0.0,
    "intense": 0.0,
    "beautiful": 0.0,
    "neo_zouk": 0.0,
    "pop": 0.0,
    "reggaeton": 0.0,
    "trippy": 0.0,
    "world": 0.0,
    "other": 0.08,
}
LANE_MAX = 0.34
LANE_LABELS = {
    "chill": "Chill",
    "energy": "Energy",
    "rnb": "R&B",
    "kizouk": "Kizouk",
    "lamba": "Lamba",
    "trancy": "Trancy / house",
    "hiphop": "Hip-hop",
    "remixes": "Remixes",
    "tribal": "Tribal",
    "bassy": "Bassy",
    "experimental": "Experimental",
    "intense": "Intense",
    "beautiful": "Beautiful",
    "classics": "Classics",
    "neo_zouk": "Neo Zouk",
    "pop": "Pop",
    "nostalgia": "Nostalgia",
    "reggaeton": "Reggaeton",
    "trippy": "Trippy",
    "world": "World",
    "other": "Other",
}

_TOP_FOLDER_LANE = {
    "chill": "chill",
    "energy": "energy",
    "jr&b": "rnb",
    "jrb": "rnb",
    "r&b": "rnb",
    "rnb": "rnb",
    "neo soul": "rnb",
    "kizouk": "kizouk",
    "lamba": "lamba",
    "brazillian": "lamba",
    "brazilian": "lamba",
    "brazilian matter": "lamba",
    "trancy": "trancy",
    "hip hoppy": "hiphop",
    "trappy": "hiphop",
    "remixes": "remixes",
    "edits": "remixes",
    "tribal": "tribal",
    "india": "tribal",
    "bassy": "bassy",
    "experimental": "experimental",
    "intense": "intense",
    "beautiful sound": "beautiful",
    "classics": "classics",
    "neo zouk": "neo_zouk",
    "neozouk": "neo_zouk",
    "pop": "pop",
    "nostalgia": "nostalgia",
    "reggatonish": "reggaeton",
    "trippy party": "trippy",
    "middle east": "world",
    "asian": "world",
    "foreign": "world",
}


def normalize_min_fit(raw: Any = None) -> float:
    """Accept 0–1 fractions or 0–100 percents. Default is the keep floor (60%)."""
    if raw is None or raw == "":
        return DEFAULT_MIN_FIT
    try:
        num = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MIN_FIT
    if num > 1.5:
        num = num / 100.0
    return max(0.0, min(1.0, num))


def clamp_target(n: int | float | None) -> int:
    try:
        value = int(n or DEFAULT_TARGET)
    except (TypeError, ValueError):
        value = DEFAULT_TARGET
    return max(MIN_TARGET, min(MAX_TARGET, value))


def clamp_chunk(n: int | float | None) -> int:
    try:
        value = int(n or DEFAULT_CHUNK)
    except (TypeError, ValueError):
        value = DEFAULT_CHUNK
    return max(MIN_CHUNK, min(MAX_CHUNK, value))


def chunk_tracks(tracks: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    n = max(1, int(size))
    return [tracks[i : i + n] for i in range(0, len(tracks), n)]


def recency_score(first_seen: float | None, *, now: float | None = None) -> float:
    import time

    if not first_seen:
        return 0.12
    age_days = max(0.0, (now or time.time()) - float(first_seen)) / 86400.0
    if age_days <= 14:
        return 1.0
    if age_days <= 45:
        return 0.78
    if age_days <= 90:
        return 0.45
    if age_days <= 180:
        return 0.22
    return 0.1


def rank_score(track: dict[str, Any], *, now: float | None = None) -> float:
    fit = float(track.get("fit") or 0.0)
    recency = recency_score(track.get("first_seen") or track.get("mtime"), now=now)
    return round(0.72 * fit + 0.28 * recency, 4)


def normalize_lane_shares(raw: Optional[dict[str, Any]] = None) -> dict[str, float]:
    """Coerce UI percents or fractions. Keep typed values; only scale if over 100%."""
    out = {lane: 0.0 for lane in LANE_SHARE}
    src = raw if isinstance(raw, dict) else {}
    any_set = False
    for lane in LANE_SHARE:
        if lane not in src:
            continue
        try:
            num = float(src[lane])
        except (TypeError, ValueError):
            continue
        if num > 1.5:
            num = num / 100.0
        out[lane] = max(0.0, min(1.0, num))
        any_set = True
    if not any_set:
        return dict(LANE_SHARE)
    total = sum(out.values())
    if total <= 0:
        return dict(LANE_SHARE)
    if total > 1.0 + 1e-6:
        return {lane: round(out[lane] / total, 4) for lane in LANE_SHARE}
    return {lane: round(out[lane], 4) for lane in LANE_SHARE}


def shares_were_provided(raw: Optional[dict[str, Any]]) -> bool:
    if not isinstance(raw, dict):
        return False
    return any(lane in raw for lane in LANE_SHARE)


def crate_lane(track: dict[str, Any]) -> str:
    """Crate lane from top folder first, then vibe/genre keywords."""
    rel = str(track.get("relative_path") or "").replace("\\", "/")
    top = rel.split("/", 1)[0].strip().lower()
    if top in _TOP_FOLDER_LANE:
        return _TOP_FOLDER_LANE[top]
    blob = f"{rel} {track.get('vibe') or ''} {track.get('genre') or ''}".lower()
    if any(k in blob for k in ("jr&b", "jrb", "r&b", "rnb", "neo soul", "kizouk r")):
        return "rnb"
    if any(k in blob for k in ("kizouk", "kizomba", "urban kiz")):
        return "kizouk"
    if any(k in blob for k in ("lamba", "brazil", "brazilian", "brazillian")):
        return "lamba"
    if any(k in blob for k in ("tribal", "india", "shaman", "downtemple", "psytrance")):
        return "tribal"
    if any(k in blob for k in ("hip hop", "hiphop", "trappy", "trap")):
        return "hiphop"
    if any(k in blob for k in ("/remixes/", "remixes/", "edits/")):
        return "remixes"
    if any(k in blob for k in ("trancy", "trance", "housey", "deep house", "tech house")):
        return "trancy"
    if "bassy" in blob:
        return "bassy"
    if "experimental" in blob:
        return "experimental"
    if "intense" in blob:
        return "intense"
    if "beautiful" in blob:
        return "beautiful"
    if "classic" in blob:
        return "classics"
    if "neo zouk" in blob or "neozouk" in blob:
        return "neo_zouk"
    if any(k in blob for k in ("reggaeton", "reggaton")):
        return "reggaeton"
    if "nostalgia" in blob:
        return "nostalgia"
    if "trippy" in blob:
        return "trippy"
    if any(k in blob for k in ("middle east", "asian", "foreign", "world")):
        return "world"
    if any(k in blob for k in ("energy", "light", "party", "peak", "openers")):
        return "energy"
    if any(
        k in blob
        for k in ("chill", "mystical", "journey", "fire", "lounge", "closers")
    ):
        return "chill"
    if blob.strip().startswith("pop") or "/pop/" in blob or blob.endswith(" pop"):
        return "pop"
    fam = genre_family(track.get("genre"), track.get("vibe"))
    if fam == "rnb_soul_zouk":
        return "rnb"
    if fam == "house_dance":
        return "trancy"
    if fam == "hiphop":
        return "hiphop"
    if fam == "psy_tribal_world":
        return "tribal"
    return "other"


def interleave_by_lane(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rotate lanes so Gemini does not hear 12 Chill in a row."""
    buckets: dict[str, list[dict[str, Any]]] = {lane: [] for lane in LANE_SHARE}
    for track in tracks:
        buckets.setdefault(crate_lane(track), []).append(track)
    out: list[dict[str, Any]] = []
    while any(buckets.values()):
        for lane in LANE_SHARE:
            if buckets.get(lane):
                out.append(buckets[lane].pop(0))
    return out


def mix_summary(playlist: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {lane: 0 for lane in LANE_SHARE}
    for track in playlist:
        lane = track.get("lane") or crate_lane(track)
        counts[lane] = counts.get(lane, 0) + 1
    return counts


def assemble_job_busy(job: dict[str, Any] | None) -> bool:
    """True while an assemble job is queued or running."""
    if not job:
        return False
    return bool(job.get("id") and job.get("status") in {"running", "queued"})
