"""Musical key helpers (VDJ Tags Key → Camelot wheel compatibility)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# Camelot: number 1–12 + letter A (minor) / B (major)
_MAJOR = {
    "b": 1,
    "f#": 2,
    "gb": 2,
    "db": 3,
    "c#": 3,
    "ab": 4,
    "g#": 4,
    "eb": 5,
    "d#": 5,
    "bb": 6,
    "a#": 6,
    "f": 7,
    "c": 8,
    "g": 9,
    "d": 10,
    "a": 11,
    "e": 12,
}
_MINOR = {
    "g#m": 1,
    "abm": 1,
    "ebm": 2,
    "d#m": 2,
    "bbm": 3,
    "a#m": 3,
    "fm": 4,
    "cm": 5,
    "gm": 6,
    "dm": 7,
    "am": 8,
    "em": 9,
    "bm": 10,
    "f#m": 11,
    "gbm": 11,
    "c#m": 12,
    "dbm": 12,
}

# Open Key style sometimes stored as 1d/1m etc. — map via major/minor tables after normalize.
_OPEN_KEY_RE = re.compile(r"^(\d{1,2})\s*([dmab])$", re.I)


def song_key_from_element(song) -> str:
    """Best-effort VDJ key: Tags.Key / Tags.Harmonic, then Scan.Key."""
    if song is None:
        return ""
    tags = song.find("Tags")
    if tags is not None:
        tagged = (tags.get("Key") or tags.get("Harmonic") or "").strip()
        if tagged:
            return tagged
    scan = song.find("Scan")
    if scan is not None:
        scanned = (scan.get("Key") or "").strip()
        if scanned:
            return scanned
    return ""


def normalize_key_string(raw: str | None) -> str:
    if not raw:
        return ""
    s = raw.strip().replace(" ", "")
    s = s.replace("maj", "").replace("min", "m")
    s = s.replace("Major", "").replace("Minor", "m")
    return s


def key_to_camelot(raw: str | None) -> Optional[str]:
    """
    Convert VDJ key strings like 'F#m', 'Am', 'Bb' to Camelot codes '11A', '8A', '6B'.
    Returns None when unparseable.
    """
    s = normalize_key_string(raw)
    if not s:
        return None
    low = s.lower().replace("♯", "#").replace("♭", "b")

    # Already Camelot? e.g. 11A / 8B
    m = re.fullmatch(r"(\d{1,2})\s*([ab])", low)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 12:
            return f"{n}{m.group(2).upper()}"

    # Open Key: 1d = 8B-ish mapping — treat d as major, m as minor with shifted numbers
    # VirtualDJ usually stores classical keys; skip open key complexity if not present.

    if low in _MINOR:
        return f"{_MINOR[low]}A"
    if low.endswith("m") and low in _MINOR:
        return f"{_MINOR[low]}A"
    # major
    major = low[:-1] if low.endswith("m") else low
    # strip trailing major markers
    major = major.rstrip("m")
    if major in _MAJOR:
        # if original ended with m it's minor — already handled
        if low.endswith("m") and low not in _MINOR:
            # unknown minor
            return None
        if not low.endswith("m"):
            return f"{_MAJOR[major]}B"
    if low in _MAJOR:
        return f"{_MAJOR[low]}B"
    return None


def camelot_compatible(source: str | None, candidate: str | None) -> bool:
    """
    True when keys are mix-friendly:
      - same Camelot code
      - relative major/minor (same number, other letter)
      - ±1 number, same letter (energy boost/drop on wheel)
    """
    a = key_to_camelot(source)
    b = key_to_camelot(candidate)
    if not a or not b:
        return False
    na, la = int(a[:-1]), a[-1]
    nb, lb = int(b[:-1]), b[-1]
    if na == nb and la == lb:
        return True
    if na == nb and la != lb:
        return True  # relative
    if la == lb and (abs(na - nb) % 12 in {1, 11}):
        return True  # adjacent
    return False


def energy_bucket_from_folder(path: str) -> str:
    """Heuristic energy label from library path for sorting candidates."""
    p = path.lower()
    if any(x in p for x in ("chill", "ambient", "downtempo", "soft", "mellow", "mystical")):
        return "lower"
    if any(x in p for x in ("energy", "party", "peak", "bassy", "trappy", "hype", "club")):
        return "higher"
    return "same"


def unescape_xml_text(raw: str | None) -> str:
    if not raw:
        return ""
    return (
        raw.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .strip()
    )


def vibe_label_from_path(relative_path: str, library: str = "") -> str:
    """
    Human vibe label from folder path (e.g. India, Chill/Fire, Energy/Housey).
    Used when Genre tags are missing.
    """
    if not relative_path:
        return library or ""
    parts = [p for p in Path(relative_path).parts if p and p not in {".", ".."}]
    if not parts:
        return library or ""
    # Drop filename
    folders = parts[:-1] if len(parts) > 1 else []
    if not folders:
        return library or ""
    # Keep up to 2 meaningful folder levels
    label = " / ".join(folders[:2])
    return label


# Loose genre families for soft ranking (not a hard filter)
_GENRE_FAMILIES: list[tuple[str, set[str]]] = [
    (
        "rnb_soul_zouk",
        {
            "r&b",
            "rnb",
            "soul",
            "neo soul",
            "urban kiz",
            "zouk",
            "zouk remix",
            "lounge zouk",
            "kizomba",
            "afrobeats",
            "afro",
            "pop",
        },
    ),
    (
        "hiphop",
        {"hip-hop", "hip hop", "hip hop/rap", "rap", "trap", "trappy"},
    ),
    (
        "house_dance",
        {
            "deep house",
            "house",
            "tech house",
            "dance",
            "garage",
            "electronic",
            "electronica",
            "edm",
        },
    ),
    (
        "psy_tribal_world",
        {
            "tribal",
            "psy",
            "psytrance",
            "world",
            "downtemple",
            "ambient",
            "organic",
            "ethnic",
            "india",
            "shaman",
            "desert dwellers",
            "drumspyder",
            "shpongle",
            "ott ",
            " entheogenic",
            " entheogen",
        },
    ),
    (
        "rock_indie",
        {"rock", "indie", "indie rock", "alternative", "alternative & punk", "power pop"},
    ),
]


def genre_family(
    genre: str | None,
    vibe: str | None = None,
    *,
    artist: str | None = None,
    title: str | None = None,
) -> str:
    """Map free-text genre/vibe/artist into a coarse family id (or empty)."""
    blob = f"{genre or ''} {vibe or ''} {artist or ''} {title or ''}".lower()
    if not blob.strip():
        return ""
    # Folder names that strongly imply a family
    if any(
        k in blob
        for k in (
            "india",
            "tribal",
            "shaman",
            "desert dwellers",
            "downtemple",
            "mystical",
            "organic",
            "drumspyder",
        )
    ):
        return "psy_tribal_world"
    if any(
        k in blob
        for k in ("meridyun", "kiz", "zouk", "urban kiz", "saia", "kizomba")
    ):
        return "rnb_soul_zouk"
    if any(k in blob for k in ("housey", "deep house", "tech house", "club")):
        return "house_dance"
    for fam, keys in _GENRE_FAMILIES:
        if any(k in blob for k in keys):
            return fam
    return ""


def genres_compatible(
    source_genre: str | None,
    source_vibe: str | None,
    cand_genre: str | None,
    cand_vibe: str | None,
) -> bool:
    """True when genres/vibes look mix-friendly (same family or unknown)."""
    a = genre_family(source_genre, source_vibe)
    b = genre_family(cand_genre, cand_vibe)
    if not a or not b:
        return True  # unknown → don't hard-block
    return a == b


def format_genre_display(genre: str | None, vibe: str | None = None) -> str:
    """Single label for UI: prefer tag genre, else folder vibe."""
    g = (genre or "").strip()
    v = (vibe or "").strip()
    if g and v and g.lower() not in v.lower():
        return f"{g} · {v}"
    return g or v or ""


# Inbox / staging folders are not a musical genre.
_WEAK_PATH_TOKENS = (
    "add cues",
    "ready for sort",
    "ac low quality",
    "no cues found",
    "low quality skip",
    "cues sorted",
    "screenshots",
    "not sorted",
)
# Energy/mood crate names — useful for energy buckets, not genre family.
_ENERGY_ONLY_FOLDERS = {
    "energy",
    "chill",
    "party",
    "peak",
    "bassy",
    "soft",
    "mellow",
    "hype",
    "club",
    "fire",
}
_UNCLEAR_GENRE_TAGS = {
    "",
    "other",
    "unknown",
    "n/a",
    "na",
    "none",
    "misc",
    "various",
    "unclassified",
}


def vibe_for_genre_clarity(vibe: str | None) -> str:
    """Strip staging/energy-only folders so leftover text can imply a genre."""
    if not vibe:
        return ""
    cleaned = vibe
    for tok in _WEAK_PATH_TOKENS:
        cleaned = re.sub(re.escape(tok), " ", cleaned, flags=re.I)
    parts = [p.strip(" /") for p in re.split(r"[/·|,]+", cleaned) if p.strip(" /")]
    kept: list[str] = []
    for part in parts:
        low = part.lower()
        if low in _ENERGY_ONLY_FOLDERS:
            continue
        if re.fullmatch(r"[\d.\-]+", part):
            continue
        kept.append(part)
    return " / ".join(kept)


def path_genre_is_clear(genre: str | None, vibe: str | None = None) -> bool:
    """
    True when VDJ Genre tag or library folder already implies a mix family.

    Does not look at artist/title — those are for Gemini when this is false.
    Inbox paths (Add Cues, Ready, AC Low Quality) and energy-only crates
    (Energy, Chill, Party) do not count as a genre.
    """
    g = (genre or "").strip()
    if g.lower() in _UNCLEAR_GENRE_TAGS:
        g = ""
    v = vibe_for_genre_clarity(vibe)
    return bool(genre_family(g, v))
