"""Mix timing from VDJ cue names: complementary frequencies like a puzzle.

A melodic downsection is missing drums; a drum bed is missing melody.
Recommendations prefer incoming sections that fill the outgoing hole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .musical_key import unescape_xml_text

LAYERS = ("drums", "bass", "melody", "vocals")
LEAD_LAYERS = frozenset({"melody", "vocals"})

_SKIP_NAME = re.compile(
    r"^(tempo\b|energy\s*\d+$|cue\s*\d+$|loop\s*\d+$|automix$)",
    re.I,
)
_POI_TAG_RE = re.compile(r"<Poi\b([^>]*)/?>", re.I)
_ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


@dataclass(frozen=True)
class Marker:
    name: str
    pos: float
    kind: str = "cue"
    structure: str = ""
    layers: frozenset[str] = field(default_factory=frozenset)
    missing: frozenset[str] = field(default_factory=frozenset)
    label: str = ""

    def to_window(self) -> dict[str, Any]:
        return {
            "time": format_timestamp(self.pos),
            "pos": self.pos,
            "label": self.label or self.name,
            "structure": self.structure,
            "present": sorted(self.layers),
            "missing": sorted(self.missing),
        }


def format_timestamp(sec: float | int | None) -> str:
    if sec is None:
        return "?:??"
    try:
        value = max(0.0, float(sec))
    except (TypeError, ValueError):
        return "?:??"
    minutes = int(value // 60)
    seconds = int(round(value - minutes * 60))
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}:{seconds:02d}"


def _tokens(name: str) -> list[str]:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (name or "").lower())
    return [t for t in cleaned.split() if t]


def _compact(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def classify_marker(
    name: str,
    *,
    pos: float = 0.0,
    kind: str = "cue",
) -> Optional[Marker]:
    raw = (name or "").strip()
    if not raw or _SKIP_NAME.match(raw):
        return None

    tokens = set(_tokens(raw))
    compact = _compact(raw)
    layers: set[str] = set()
    structure = ""

    if tokens & {"breakdown", "down"} or "downsection" in compact or compact == "down":
        structure = "breakdown"
        layers.add("melody")
    elif "outro" in tokens or compact in {"o", "ol", "outro"}:
        structure = "outro"
    elif "intro" in tokens or compact in {"i", "i2", "il", "intro"}:
        structure = "intro"
    elif "drop" in tokens or compact in {"drop", "d2"} and "drum" not in compact:
        if compact == "d2":
            structure = ""
        else:
            structure = "drop"
            layers.add("drums")
            layers.add("bass")
    elif "build" in tokens:
        structure = "build"
        layers.add("drums")
    elif "verse" in tokens:
        structure = "verse"
    elif "chorus" in tokens or compact in {"c", "cl", "chorus"}:
        structure = "chorus"
        layers.add("drums")
        layers.add("melody")
    elif "bridge" in tokens:
        structure = "bridge"

    if compact == "d2" or tokens & {"drum", "drums", "snare"} or compact in {
        "d",
        "dl",
        "drum",
        "drums",
    }:
        layers.add("drums")
    if "drum" in compact and compact not in {"drumspyder"}:
        layers.add("drums")
    if tokens & {"bass", "sub"} or "bass" in compact:
        layers.add("bass")
    if tokens & {"melody", "melodic", "synth", "piano", "guitar", "strings", "lick", "instr", "instrumental", "pad"}:
        layers.add("melody")
    if compact in {"m", "ml", "s", "sl", "p", "pl", "melody", "synth", "piano"}:
        layers.add("melody")
    if "melody" in compact or "synth" in compact or "piano" in compact:
        layers.add("melody")
    if tokens & {"vocal", "vocals", "voice", "acapella", "a cappella"} or compact in {
        "v",
        "vl",
        "vocal",
        "vocals",
        "voice",
    }:
        layers.add("vocals")
    if "vocal" in compact or "voice" in compact:
        layers.add("vocals")

    # Bare Intro is a time, not a frequency claim. Outro usually thins to melody.
    if structure == "outro" and not layers:
        layers.add("melody")

    missing: set[str] = set()
    if structure in {"breakdown", "outro"} or (
        "melody" in layers and "drums" not in layers
    ):
        if "drums" not in layers:
            missing.add("drums")
        if "bass" not in layers:
            missing.add("bass")
        if not layers:
            layers.add("melody")
    if "drums" in layers and not (layers & LEAD_LAYERS):
        missing.add("melody")
    if "vocals" in layers and "drums" not in layers:
        missing.add("drums")
    if structure == "intro" and "drums" in layers and "melody" not in layers:
        missing.add("melody")

    label = raw
    return Marker(
        name=raw,
        pos=float(pos or 0.0),
        kind=kind if kind in {"cue", "loop"} else "cue",
        structure=structure,
        layers=frozenset(layers),
        missing=frozenset(missing),
        label=label,
    )


def parse_markers(raw: Iterable[dict[str, Any] | Any]) -> list[Marker]:
    markers: list[Marker] = []
    for item in raw or []:
        if isinstance(item, Marker):
            markers.append(item)
            continue
        if hasattr(item, "name") and hasattr(item, "pos"):
            name = str(getattr(item, "name") or "")
            pos = float(getattr(item, "pos") or 0.0)
            kind = str(getattr(item, "kind") or "cue")
        elif isinstance(item, dict):
            name = str(item.get("name") or "")
            try:
                pos = float(item.get("pos") or 0.0)
            except (TypeError, ValueError):
                pos = 0.0
            kind = str(item.get("kind") or "cue")
        else:
            continue
        marker = classify_marker(name, pos=pos, kind=kind)
        if marker is not None:
            markers.append(marker)
    markers.sort(key=lambda m: (m.pos, m.kind != "cue"))
    return markers


def parse_pois_from_song_xml(chunk: str) -> list[dict[str, Any]]:
    """Lightweight POI extract from a database.xml Song chunk."""
    if not chunk:
        return []
    pois: list[dict[str, Any]] = []
    for match in _POI_TAG_RE.finditer(chunk):
        attrs = {k.lower(): v for k, v in _ATTR_RE.findall(match.group(1) or "")}
        kind = (attrs.get("type") or "").lower()
        if kind not in {"cue", "loop"}:
            continue
        num = attrs.get("num") or "0"
        if kind == "cue" and num == "0":
            continue
        name = unescape_xml_text(attrs.get("name") or "")
        if not name or _SKIP_NAME.match(name):
            continue
        try:
            pos = float(attrs.get("pos") or 0.0)
        except (TypeError, ValueError):
            pos = 0.0
        pois.append({"name": name, "pos": pos, "kind": kind})
    pois.sort(key=lambda p: p["pos"])
    return pois[:28]


def mix_out_windows(
    markers: list[Marker],
    *,
    song_length: float | None = None,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Outgoing mix holes — later breakdowns / outros first."""
    if not markers:
        return []
    length = float(song_length or 0.0)
    if length <= 0:
        length = max(m.pos for m in markers) + 32.0

    def rank(marker: Marker) -> tuple:
        late = 1 if marker.pos >= length * 0.35 else 0
        hole = 1 if marker.missing else 0
        # Mix during the downsection, not after the track has already ended.
        breakdown = 2 if marker.structure == "breakdown" else 0
        outro = 1 if marker.structure == "outro" else 0
        return (hole, breakdown, outro, late, marker.pos)

    ranked = sorted(markers, key=rank, reverse=True)
    picked: list[Marker] = []
    for marker in ranked:
        if marker.structure == "intro" and marker.pos < length * 0.25:
            continue
        picked.append(marker)
        if len(picked) >= limit:
            break
    if not picked:
        picked = markers[-1:]
    return [m.to_window() for m in picked]


def timing_score_pair(
    outgoing: Marker,
    incoming: Marker,
    *,
    out_length: float | None = None,
    in_length: float | None = None,
) -> float:
    """How well incoming frequencies fill the outgoing hole."""
    if incoming.structure == "outro":
        return -6.0

    score = 0.0
    fills = outgoing.missing & incoming.layers
    if "drums" in fills:
        score += 10.0
    if "melody" in fills:
        score += 8.0
    if "vocals" in fills:
        score += 7.0
    if "bass" in fills:
        score += 4.0

    clash = (outgoing.layers & incoming.layers) & LEAD_LAYERS
    if "vocals" in clash:
        score -= 6.0
    if "melody" in clash and "drums" not in incoming.layers:
        score -= 4.0

    if outgoing.structure in {"breakdown", "outro"} and incoming.structure in {
        "intro",
        "drop",
    }:
        score += 4.0
    if outgoing.structure in {"breakdown", "outro"} and "drums" in incoming.layers:
        score += 3.0
    if outgoing.structure == "drop" and incoming.structure == "breakdown":
        score += 2.0

    out_len = float(out_length or 0.0)
    in_len = float(in_length or 0.0)
    if out_len > 0 and outgoing.pos >= out_len * 0.35:
        score += 2.0
    if in_len > 0 and incoming.pos <= in_len * 0.45:
        score += 2.0
    elif incoming.pos <= 90:
        score += 1.0
    # Late mix-in (second drop / outro-adjacent) must not beat an early drum bed.
    if in_len > 0 and incoming.pos >= in_len * 0.55:
        score -= 12.0
    if in_len > 0 and incoming.pos >= in_len * 0.75:
        score -= 6.0
    return score


def _fills(outgoing: Marker, incoming: Marker) -> list[str]:
    return [layer for layer in LAYERS if layer in outgoing.missing and layer in incoming.layers]


def best_timing(
    source_markers: list[Marker],
    cand_markers: list[Marker],
    *,
    source_length: float | None = None,
    cand_length: float | None = None,
) -> Optional[dict[str, Any]]:
    """Best outgoing hole → incoming fill pair for one candidate."""
    if not source_markers or not cand_markers:
        return None
    outgoing_pool = source_markers
    windows = mix_out_windows(source_markers, song_length=source_length, limit=3)
    window_pos = {float(w["pos"]) for w in windows}
    preferred = [m for m in source_markers if m.pos in window_pos]
    if preferred:
        outgoing_pool = preferred

    incoming_pool = [m for m in cand_markers if m.structure != "outro"]
    in_len = float(cand_length or 0.0)
    if in_len > 0:
        early = [m for m in incoming_pool if m.pos < in_len * 0.6]
        if early:
            incoming_pool = early
    if not incoming_pool:
        incoming_pool = cand_markers

    best: Optional[tuple[float, float, Marker, Marker]] = None
    for outgoing in outgoing_pool:
        for incoming in incoming_pool:
            score = timing_score_pair(
                outgoing,
                incoming,
                out_length=source_length,
                in_length=cand_length,
            )
            # Prefer earlier mix-in, then the downsection over the dying outro
            breakdown = 1 if outgoing.structure == "breakdown" else 0
            key = (score, -incoming.pos, breakdown)
            prev = (
                (best[0], -best[1], 1 if best[2].structure == "breakdown" else 0)
                if best is not None
                else None
            )
            if prev is None or key > prev:
                best = (score, incoming.pos, outgoing, incoming)
    if best is None or best[0] < 4.0:
        return None
    score, _in_pos, outgoing, incoming = best
    fills = _fills(outgoing, incoming)
    summary = (
        f"{format_timestamp(outgoing.pos)} {outgoing.label} → "
        f"{format_timestamp(incoming.pos)} {incoming.label}"
    )
    if fills:
        summary = f"{summary} · fills {' + '.join(fills)}"
    return {
        "score": round(score, 2),
        "out_pos": outgoing.pos,
        "out_time": format_timestamp(outgoing.pos),
        "out_label": outgoing.label,
        "out_structure": outgoing.structure,
        "out_present": sorted(outgoing.layers),
        "in_pos": incoming.pos,
        "in_time": format_timestamp(incoming.pos),
        "in_label": incoming.label,
        "in_structure": incoming.structure,
        "in_present": sorted(incoming.layers),
        "fills": fills,
        "missing": sorted(outgoing.missing),
        "summary": summary,
    }


def markers_from_cue_summary(cues: Any) -> tuple[list[Marker], Optional[float]]:
    points = getattr(cues, "points", None) or []
    length = getattr(cues, "song_length", None)
    try:
        length_f = float(length) if length not in (None, "") else None
    except (TypeError, ValueError):
        length_f = None
    return parse_markers(points), length_f
