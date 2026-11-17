// Builds public/guide.xml.gz: epgshare01 programmes for the channels in public/playlist.m3u, next 48 hours.
import { createWriteStream, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { finished } from 'node:stream/promises';
import { createGunzip, createGzip } from 'node:zlib';

const SOURCE_URL = 'https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz';
const PUBLIC = join(import.meta.dirname, '..', 'public');
const PLAYLIST = join(PUBLIC, 'playlist.m3u');
const OUTPUT = join(PUBLIC, 'guide.xml.gz');
const USER_AGENT = 'Mozilla/5.0 (Linux; Android 11) OTT Navigator'; // host refuses default agents
const WINDOW_MS = 48 * 3600_000;
const QUALITY_TOKENS = new Set(['hd', 'fhd', 'uhd', 'sd', '4k', 'hevc']);
// One complete top-level <channel>/<programme> element (self-closing or with body)
const ELEMENT_RE = /<(channel|programme)\b([^>]*?)(\/>|>[\s\S]*?<\/\1>)/g;

async function main() {
  const wanted = playlistIds();
  console.log(`${wanted.size} channel ids in playlist`);
  const res = await fetch(SOURCE_URL, { headers: { 'User-Agent': USER_AGENT }, signal: AbortSignal.timeout(20 * 60_000) });
  if (!res.ok || !res.body) throw new Error(`${SOURCE_URL}: HTTP ${res.status}`);
  const source = Readable.fromWeb(res.body).pipe(createGunzip()).setEncoding('utf8');
  const gzip = createGzip();
  const done = finished(gzip.pipe(createWriteStream(OUTPUT)));
  const matched = await copyMatching(source, (text) => gzip.write(text), wanted);
  gzip.end();
  await done;
  console.log(`matched ${matched} channels; wrote ${OUTPUT} (${Math.floor(statSync(OUTPUT).size / 1024)} KB)`);
}

// Maps normalized key to playlist tvg-id, by id and by name+country
function playlistIds() {
  const pairs = readFileSync(PLAYLIST, 'utf8').split('\n').flatMap((line) => {
    const tvgId = /tvg-id="([^"]+)"/.exec(line)?.[1];
    if (!tvgId) return [];
    const name = line.slice(line.lastIndexOf(',') + 1);
    return [[normalize(tvgId), tvgId], [normalizeName(name, countryOf(tvgId)), tvgId]] as const;
  });
  return new Map(pairs.reverse()); // reversed so first playlist hit wins
}

// Streams source XMLTV, writes channels/programmes that map to a playlist id; buffer holds one partial element at most
async function copyMatching(source: AsyncIterable<string>, write: (text: string) => void, wanted: Map<string, string>) {
  const now = Date.now();
  const epgToPlaylist = emptyIdMap();
  const written = new Set(epgToPlaylist.values());
  write('<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="iptv-live">\n');
  let buffer = '';
  for await (const chunk of source) {
    buffer += chunk;
    let consumed = 0;
    for (const match of buffer.matchAll(ELEMENT_RE)) {
      const [element, tag, attrs] = match;
      consumed = (match.index ?? 0) + element.length;
      if (tag === 'channel') {
        const epgId = attr(attrs, 'id');
        const name = decode(/<display-name[^>]*>([^<]*)<\/display-name>/.exec(element)?.[1] ?? '');
        const tvgId = wanted.get(normalize(epgId)) ?? wanted.get(normalizeName(name, countryOf(epgId)));
        if (!tvgId || written.has(tvgId)) continue;
        epgToPlaylist.set(epgId, tvgId);
        written.add(tvgId);
        write(setAttr(element, 'id', tvgId));
      } else {
        const tvgId = epgToPlaylist.get(attr(attrs, 'channel'));
        if (tvgId && inWindow(attrs, now)) write(setAttr(element, 'channel', tvgId));
      }
    }
    // keep only tail after last full element (or last open tag) so memory stays flat on multi-GB feed
    const tail = buffer.slice(consumed);
    const open = tail.search(/<(channel|programme)\b/);
    buffer = open >= 0 ? tail.slice(open) : tail.slice(-64);
  }
  write('</tv>\n');
  return written.size;
}

const emptyIdMap = (): Map<string, string> => new Map();

const attr = (attrs: string, name: string) => decode(new RegExp(`\\b${name}="([^"]*)"`).exec(attrs)?.[1] ?? '');

function setAttr(element: string, name: string, value: string) {
  const escaped = value.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  return element.replace(new RegExp(`^(<\\w+\\b[^>]*?\\b${name}=")[^"]*"`), `$1${escaped}"`);
}

function decode(text: string) {
  return text
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&amp;/g, '&');
}

function inWindow(attrs: string, now: number) {
  const start = parseXmltvTime(attr(attrs, 'start'));
  const stop = parseXmltvTime(attr(attrs, 'stop'));
  if (Number.isNaN(start) || Number.isNaN(stop)) return false;
  return stop > now && start < now + WINDOW_MS;
}

// "20261117120000 +0800" to epoch ms; NaN when malformed
function parseXmltvTime(value: string) {
  const m = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\s*([+-])(\d{2})(\d{2})$/.exec(value.trim());
  if (!m) return NaN;
  const [, y, mo, d, h, mi, s, sign, oh, om] = m;
  const offsetMs = (Number(oh) * 60 + Number(om)) * 60_000 * (sign === '-' ? -1 : 1);
  return Date.UTC(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s)) - offsetMs;
}

function normalize(channelId: string) {
  const base = channelId.split('@')[0].toLowerCase();
  return base.split(/[^a-z0-9]+/).filter((p) => p && !QUALITY_TOKENS.has(p)).join('');
}

function normalizeName(name: string, country: string) {
  const words = name.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w && !QUALITY_TOKENS.has(w));
  return `name:${words.join('')}.${country}`;
}

function countryOf(channelId: string) {
  return /\.([a-z]{2})(?:@|$)/.exec(channelId.toLowerCase())?.[1] ?? '';
}

await main();
