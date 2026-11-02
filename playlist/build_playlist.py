# Builds public/playlist.m3u from iptv-org's language + country lists, keeping only streams that answer.
import dataclasses
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

LANGUAGE_URL = "https://iptv-org.github.io/iptv/index.language.m3u"
COUNTRY_URL = "https://iptv-org.github.io/iptv/index.country.m3u"
GUIDE_URL = "https://tedwin-main.github.io/iptv-live/guide.xml.gz"
OUTPUT = Path(__file__).resolve().parent.parent / "public" / "playlist.m3u"
DEFAULT_USER_AGENT = "Mozilla/5.0 (Linux; Android 11) OTT Navigator"
PROBE_THREADS = 64
PROBE_TIMEOUT_S = 10
CATEGORIES = ("English", "Chinese", "Malay", "Korean", "Japanese", "Malaysia")
CHINESE_DIALECTS = {"Cantonese", "Hakka", "Hokkien", "Teochew"}
MALAY_LANGUAGES = {"Malay", "Indonesian"}


@dataclass
class Entry:
    info: str
    options: list[str] = field(default_factory=list)
    url: str = ""

    @property
    def group(self) -> str:
        match = re.search(r'group-title="([^"]*)"', self.info)
        return match.group(1) if match else "Undefined"

    @property
    def name(self) -> str:
        return self.info.rsplit(",", 1)[-1].strip()

    def option(self, key: str) -> str | None:
        prefix = f"#EXTVLCOPT:http-{key}="
        return next((o[len(prefix):] for o in self.options if o.startswith(prefix)), None)

    def with_group(self, group: str) -> "Entry":
        info = re.sub(r'group-title="[^"]*"', f'group-title="{group}"', self.info)
        return dataclasses.replace(self, info=info, options=list(self.options))

    def to_m3u(self) -> str:
        return "\n".join([self.info, *self.options, self.url])


def main() -> None:
    entries = collect_entries()
    by_url = {e.url: e for e in entries}
    print(f"fetched {len(entries)} entries, {len(by_url)} unique streams")
    with ThreadPoolExecutor(PROBE_THREADS) as pool:
        verdict_of = dict(zip(by_url, pool.map(probe, by_url.values())))
    kept = [e for e in entries if verdict_of[e.url] == "alive"]
    kept.sort(key=lambda e: (CATEGORIES.index(e.group), e.name.lower()))
    write_playlist(kept)
    verdicts = list(verdict_of.values())
    for verdict in ("alive", "dead"):
        print(f"{verdict}: {verdicts.count(verdict)} streams")
    groups = [e.group for e in kept]
    for category in CATEGORIES:
        print(f"{category}: {groups.count(category)} channels")
    print(f"wrote {len(kept)} channel entries to {OUTPUT}")


def collect_entries() -> list[Entry]:
    """Language entries regrouped into CATEGORIES, plus Malaysia's channels from the country list."""
    by_language = [
        entry.with_group(category)
        for entry in parse_m3u(fetch_text(LANGUAGE_URL))
        if (category := category_for_language(entry.group))
    ]
    malaysian = [e for e in parse_m3u(fetch_text(COUNTRY_URL)) if e.group == "Malaysia"]
    return dedupe(by_language + malaysian)


def category_for_language(language: str) -> str | None:
    """Folds dialects into parent language; Indonesian counts as Malay. None means drop."""
    if language.endswith("English"):
        return "English"
    if language.endswith("Chinese") or language in CHINESE_DIALECTS:
        return "Chinese"
    if language in MALAY_LANGUAGES:
        return "Malay"
    if language in ("Korean", "Japanese"):
        return language
    return None


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", "replace")


def parse_m3u(text: str) -> list[Entry]:
    entries, current = [], None
    for line in (raw.strip() for raw in text.splitlines()):
        if line.startswith("#EXTINF"):
            current = Entry(info=line)
        elif current and line.startswith("#EXTVLCOPT"):
            current.options.append(line)
        elif current and line and not line.startswith("#"):
            current.url = line
            entries.append(current)
            current = None
    return entries


def dedupe(entries: list[Entry]) -> list[Entry]:
    """Drops repeats within one category; a channel stays listed under each category it fits."""
    return list({(e.group, e.url): e for e in entries}.values())


def probe(entry: Entry) -> str:
    """Returns alive or dead; 401/403 count as dead since they refuse Malaysian viewers too."""
    headers = {"User-Agent": entry.option("user-agent") or DEFAULT_USER_AGENT}
    if referrer := entry.option("referrer"):
        headers["Referer"] = referrer
    try:
        request = urllib.request.Request(entry.url, headers=headers)
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_S) as response:
            head = response.read(512)
    except Exception:
        return "dead"
    return "alive" if is_playable(entry.url, head) else "dead"


def is_playable(url: str, head: bytes) -> bool:
    if ".m3u8" in url.lower():
        return head.lstrip().startswith(b"#EXTM3U")
    return len(head) > 0


def write_playlist(entries: list[Entry]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(e.to_m3u() for e in entries)
    OUTPUT.write_text(f'#EXTM3U url-tvg="{GUIDE_URL}"\n{body}\n', encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
