# iptv-live

Free channels from [iptv-org](https://github.com/iptv-org/iptv), grouped into 6 categories, with dead streams removed
and a 48-hour TV guide.

| Category | Includes |
|---|---|
| English | English + creoles (e.g. Islander Creole English) |
| Chinese | Chinese, Mandarin, Yue (Cantonese), Min Nan (Hokkien/Teochew), Hakka, other Chinese dialects |
| Malay | Malay + Indonesian (not Malayalam) |
| Korean | Korean |
| Japanese | Japanese |
| Malaysia | Every channel from Malaysia, also listed under its language |
| Singapore | Every channel from Singapore, also listed under its language |

All other languages are dropped. Rebuilt every day at 04:00 Malaysia time by GitHub Actions.

| What | URL |
|---|---|
| Playlist | `https://tedwin-main.github.io/iptv-live/playlist.m3u` |
| Guide (XMLTV) | `https://tedwin-main.github.io/iptv-live/guide.xml.gz` |

The playlist header points at the guide (`url-tvg`), so most players pick it up on their own.

## How it works

- `playlist/build_playlist.py`: fetches `index.language.m3u` (mapped to the categories above) plus Malaysia
  and Singapore from `index.country.m3u`, then requests every stream once.
  - **Kept:** answers 2xx with a real HLS playlist.
  - **Dropped:** no DNS, timeout, refused connection, any 4xx/5xx (401/403 included: tested from Malaysia, these
    refuse there too), or a body that is not HLS.
- `guide/build_guide.py`: streams epgshare01's all-sources XMLTV feed, matches channels by id (quality tags like
  `HD` removed) or by name + country, and keeps the next 48 hours.

## Run locally

```bash
python3 -I playlist/build_playlist.py   # ~7 min, writes public/playlist.m3u
python3 -I guide/build_guide.py         # ~30 s, writes public/guide.xml.gz
```

Run it again on demand from the Actions tab: **refresh-channel-list → Run workflow**.
