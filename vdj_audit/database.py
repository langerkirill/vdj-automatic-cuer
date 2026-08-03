"""VirtualDJ database loading for cue audits."""

from .common import *

def preprocess_xml(xml_content: str) -> str:
    xml_content = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", xml_content)
    xml_content = re.sub(r"(</[^>]+>)\s*\1+", r"\1", xml_content)
    xml_content = re.sub(
        r"(</VirtualDJ_Database>)\s*</VirtualDJ_Database>",
        r"\1",
        xml_content,
    )
    if "</VirtualDJ_Database>" in xml_content:
        xml_content = (
            xml_content.split("</VirtualDJ_Database>")[0]
            + "</VirtualDJ_Database>"
        )
    return xml_content


def parse_database(database_path: Path) -> ET.Element:
    # Binary read preserves large files; content is only used for one parse tree.
    raw = database_path.read_bytes().decode("utf-8")
    return ET.fromstring(preprocess_xml(raw))


def _song_to_track(audio_path: str, song: ET.Element) -> Track:
    tags = song.find("Tags")
    infos = song.find("Infos")
    scan = song.find("Scan")
    title = tags.get("Title", "") if tags is not None else ""
    artist = tags.get("Author", "") if tags is not None else ""
    length = float(infos.get("SongLength", "0")) if infos is not None else 0.0
    beatgrid = None
    scan_phase = None
    scan_bpm = None
    pois = []

    if scan is not None:
        try:
            if scan.get("Phase") not in (None, ""):
                scan_phase = float(scan.get("Phase"))
            if scan.get("Bpm") not in (None, ""):
                raw_bpm = float(scan.get("Bpm"))
                scan_bpm = 60.0 / raw_bpm if 0 < raw_bpm < 5 else raw_bpm
        except ValueError:
            pass

    for poi in song.findall("Poi"):
        if poi.get("Type") == "beatgrid":
            try:
                beatgrid = float(poi.get("Pos", "0"))
            except ValueError:
                beatgrid = None
            continue
        if poi.get("Type") not in {"cue", "loop"} or poi.get("Num", "0") == "0":
            continue

        color_value = poi.get("Color", "")
        pois.append(
            Poi(
                name=poi.get("Name", ""),
                pos=float(poi.get("Pos", "0") or 0),
                poi_type=poi.get("Type", ""),
                color_value=color_value,
                color_name=CUE_COLOR_VALUES.get(color_value, "unknown"),
                size=poi.get("Size", ""),
                slot=poi.get("Slot", ""),
            )
        )

    pois.sort(key=lambda item: item.pos)
    return Track(
        path=audio_path,
        title=title,
        artist=artist,
        length=length,
        pois=pois,
        beatgrid=beatgrid,
        scan_phase=scan_phase,
        scan_bpm=scan_bpm,
    )


def load_single_track(database_path: Path, audio_path: str) -> Optional[Track]:
    """Load one track without building a full ElementTree of the library."""
    try:
        from vdj_database_safety import read_vdj_database_text, _find_song_span
    except ImportError:
        return None

    content = read_vdj_database_text(database_path)
    span = _find_song_span(content, audio_path)
    if span is None:
        return None
    song = ET.fromstring(content[span[0] : span[1]])
    return _song_to_track(audio_path, song)


def load_tracks(database_path: Path, audio_paths: list[str]) -> list[Track]:
    # Prefer surgical single-song loads for small batches (post-cue audits).
    if len(audio_paths) <= 8:
        tracks = []
        for audio_path in audio_paths:
            track = load_single_track(database_path, audio_path)
            if track is not None:
                tracks.append(track)
        if tracks:
            return tracks

    root = parse_database(database_path)
    songs = {song.get("FilePath", ""): song for song in root.findall("Song")}
    tracks = []

    for audio_path in audio_paths:
        song = songs.get(audio_path)
        if song is None:
            continue
        tracks.append(_song_to_track(audio_path, song))

    return tracks


