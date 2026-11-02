# Builds public/guide.xml.gz: epgshare01 programmes for the channels in public/playlist.m3u, next 48 hours.
import gzip
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE_URL = "https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"
PUBLIC = Path(__file__).resolve().parent.parent / "public"
PLAYLIST = PUBLIC / "playlist.m3u"
OUTPUT = PUBLIC / "guide.xml.gz"
USER_AGENT = "Mozilla/5.0 (Linux; Android 11) OTT Navigator"  # host refuses Python's default agent
WINDOW = timedelta(hours=48)
QUALITY_TOKENS = {"hd", "fhd", "uhd", "sd", "4k", "hevc"}


def main() -> None:
    wanted = playlist_ids()
    print(f"{len(wanted)} channel ids in playlist")
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response, \
            gzip.GzipFile(fileobj=response) as source, \
            gzip.open(OUTPUT, "wt", encoding="utf-8") as out:
        matched = copy_matching(source, out, wanted)
    print(f"matched {matched} channels; wrote {OUTPUT} ({OUTPUT.stat().st_size // 1024} KB)")


def playlist_ids() -> dict[str, str]:
    """Maps normalized key to playlist tvg-id, by id and by name+country."""
    keys = {}
    for line in PLAYLIST.read_text(encoding="utf-8").splitlines():
        id_match = re.search(r'tvg-id="([^"]+)"', line)
        if not id_match:
            continue
        tvg_id = id_match.group(1)
        keys.setdefault(normalize(tvg_id), tvg_id)
        keys.setdefault(normalize_name(line.rsplit(",", 1)[-1], country_of(tvg_id)), tvg_id)
    return keys


def copy_matching(source, out, wanted: dict[str, str]) -> int:
    """Streams source XMLTV, writes channels/programmes that map to a playlist id."""
    now = datetime.now(timezone.utc)
    epg_to_playlist: dict[str, str] = {}
    written: set[str] = set()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="iptv-live">\n')
    events = ET.iterparse(source, events=("start", "end"))
    _, root = next(events)
    for event, element in events:
        if event != "end" or element.tag not in ("channel", "programme"):
            continue
        if element.tag == "channel":
            write_channel(element, out, wanted, epg_to_playlist, written)
        else:
            write_programme(element, out, epg_to_playlist, now)
        root.clear()  # drop finished elements so memory stays flat on a multi-GB feed
    out.write("</tv>\n")
    return len(written)


def write_channel(element, out, wanted, epg_to_playlist, written) -> None:
    epg_id = element.get("id", "")
    name = element.findtext("display-name", "")
    tvg_id = wanted.get(normalize(epg_id)) or wanted.get(normalize_name(name, country_of(epg_id)))
    if not tvg_id or tvg_id in written:
        return
    epg_to_playlist[epg_id] = tvg_id
    written.add(tvg_id)
    element.set("id", tvg_id)
    out.write(ET.tostring(element, encoding="unicode"))


def write_programme(element, out, epg_to_playlist, now) -> None:
    tvg_id = epg_to_playlist.get(element.get("channel", ""))
    if not tvg_id or not in_window(element, now):
        return
    element.set("channel", tvg_id)
    out.write(ET.tostring(element, encoding="unicode"))


def in_window(element, now: datetime) -> bool:
    try:
        start = parse_xmltv_time(element.get("start", ""))
        stop = parse_xmltv_time(element.get("stop", ""))
    except ValueError:
        return False
    return stop > now and start < now + WINDOW


def parse_xmltv_time(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y%m%d%H%M%S %z")


def normalize(channel_id: str) -> str:
    base = channel_id.split("@", 1)[0].lower()
    parts = [p for p in re.split(r"[^a-z0-9]+", base) if p and p not in QUALITY_TOKENS]
    return "".join(parts)


def normalize_name(name: str, country: str) -> str:
    words = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if w and w not in QUALITY_TOKENS]
    return "name:" + "".join(words) + "." + country


def country_of(channel_id: str) -> str:
    match = re.search(r"\.([a-z]{2})(?:@|$)", channel_id.lower())
    return match.group(1) if match else ""


if __name__ == "__main__":
    sys.exit(main())
