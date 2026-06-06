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
    with database_path.open("r", encoding="utf-8") as handle:
        return ET.fromstring(preprocess_xml(handle.read()))


def load_tracks(database_path: Path, audio_paths: list[str]) -> list[Track]:
    root = parse_database(database_path)
    songs = {song.get("FilePath", ""): song for song in root.findall("Song")}
    tracks = []

    for audio_path in audio_paths:
        song = songs.get(audio_path)
        if song is None:
            continue

        tags = song.find("Tags")
        infos = song.find("Infos")
        title = tags.get("Title", "") if tags is not None else ""
        artist = tags.get("Author", "") if tags is not None else ""
        length = float(infos.get("SongLength", "0")) if infos is not None else 0.0
        beatgrid = None
        pois = []

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
        tracks.append(
            Track(
                path=audio_path,
                title=title,
                artist=artist,
                length=length,
                pois=pois,
                beatgrid=beatgrid,
            )
        )

    return tracks


