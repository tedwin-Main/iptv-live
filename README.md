# iptv-live

Free channels from [iptv-org](https://github.com/iptv-org/iptv), grouped by country, with dead streams removed and a
48-hour TV guide. Rebuilt every day at 04:00 Malaysia time by GitHub Actions.

| What | URL |
|---|---|
| Playlist | `https://tedwin-main.github.io/iptv-live/playlist.m3u` |
| Guide (XMLTV) | `https://tedwin-main.github.io/iptv-live/guide.xml.gz` |

The playlist header points at the guide (`url-tvg`), so most players pick it up on their own.

## How it works

- `playlist/build_playlist.py`: fetches `index.country.m3u` and requests every stream once.
  - **Kept:** answers 2xx with a real HLS playlist, or 401/403/451. The check runs from US servers, so
    country-locked streams answer 403 there and are kept.
  - **Dropped:** no DNS, timeout, refused connection, 4xx/5xx, or a body that is not HLS.
- `guide/build_guide.py`: streams epgshare01's all-sources XMLTV feed, matches channels by id (quality tags like
  `HD` removed) or by name + country, and keeps the next 48 hours.

## Run locally

```bash
python3 -I playlist/build_playlist.py   # ~7 min, writes public/playlist.m3u
python3 -I guide/build_guide.py         # ~30 s, writes public/guide.xml.gz
```

Run it again on demand from the Actions tab: **refresh → Run workflow**.
