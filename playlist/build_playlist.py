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
OVERSTAR_FILE = Path(__file__).resolve().parent / "overstar.m3u"  # bundled copy; live source is dead
GUIDE_URL = "https://tedwin-main.github.io/iptv-live/guide.xml.gz"
OUTPUT = Path(__file__).resolve().parent.parent / "public" / "playlist.m3u"
DEFAULT_USER_AGENT = "Mozilla/5.0 (Linux; Android 11) OTT Navigator"
PROBE_THREADS = 64
PROBE_TIMEOUT_S = 10
COUNTRY_CATEGORIES = ("Malaysia", "Singapore", "Hong Kong")
CATEGORIES = ("Overstar", "English", "Chinese", "Malay", "Korean", "Japanese", *COUNTRY_CATEGORIES)
CHINESE_DIALECTS = {"Cantonese", "Hakka", "Hokkien", "Teochew"}
MALAY_LANGUAGES = {"Malay", "Indonesian"}
MOVED_COUNTRIES = {"hk": "Hong Kong"}  # tvg-id country shown only in its own category, not its language
HK_ID_KEYS = ("tvbjade", "tvbclassic", "tvbxinghe", "phoenixchinese", "rthk")
HK_NAME_RE = re.compile(
    r"\b(?:tvb jade|tvb classic|tvb xing he|jade|viu|hoy tv|rthk|mei ah|newsworld|news world|pet club|asian drama)\b"
    r"|翡翠|明珠|無綫|无线|香港",
    re.IGNORECASE,
)


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
    def country(self) -> str:
        match = re.search(r'tvg-id="[^"]*\.([a-z]{2})(?:@[^"]*)?"', self.info)
        return match.group(1) if match else ""

    @property
    def name(self) -> str:
        return self.info.rsplit(",", 1)[-1].strip()

    @property
    def ident(self) -> str:
        match = re.search(r'tvg-id="([^"]*)"', self.info)
        return match.group(1).lower().split("@")[0] if match else ""

    def option(self, key: str) -> str | None:
        prefix = f"#EXTVLCOPT:http-{key}="
        return next((o[len(prefix):] for o in self.options if o.startswith(prefix)), None)

    def with_group(self, group: str) -> "Entry":
        if 'group-title="' in self.info:
            info = re.sub(r'group-title="[^"]*"', f'group-title="{group}"', self.info)
        else:
            info = re.sub(r'(#EXTINF:[^,\s]*)', rf'\1 group-title="{group}"', self.info)
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
    """Overstar channels, language entries regrouped into CATEGORIES, plus COUNTRY_CATEGORIES channels."""
    overstar = [
        entry.with_group("Hong Kong" if is_hong_kong(entry) else "Overstar")
        for entry in parse_m3u(OVERSTAR_FILE.read_text(encoding="utf-8"))
        if entry.url.startswith("http") and "ACCOUNT INFO" not in entry.info
    ]
    by_language = [
        entry.with_group(category)
        for entry in parse_m3u(fetch_text(LANGUAGE_URL))
        if (category := category_for_entry(entry))
    ]
    by_country = [e for e in parse_m3u(fetch_text(COUNTRY_URL)) if e.group in COUNTRY_CATEGORIES]
    return dedupe(overstar + by_language + by_country)


def category_for_entry(entry: Entry) -> str | None:
    if is_hong_kong(entry):
        return "Hong Kong"
    return MOVED_COUNTRIES.get(entry.country) or category_for_language(entry.group)


def is_hong_kong(entry: Entry) -> bool:
    """Hong Kong channels go to the Hong Kong category, whatever list they came from."""
    if entry.country == "hk":
        return True
    return any(key in entry.ident for key in HK_ID_KEYS) or bool(HK_NAME_RE.search(entry.name))


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
        elif current and line.startswith("#"):
            current.options.append(line)
        elif current and line:
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
