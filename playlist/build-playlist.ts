// Builds public/playlist.m3u from iptv-org's language + country lists, keeping only streams that answer.
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

const LANGUAGE_URL = 'https://iptv-org.github.io/iptv/index.language.m3u';
const COUNTRY_URL = 'https://iptv-org.github.io/iptv/index.country.m3u';
const GUIDE_URL = 'https://tedwin-main.github.io/iptv-live/guide.xml.gz';
const OUTPUT = join(import.meta.dirname, '..', 'public', 'playlist.m3u');
const DEFAULT_USER_AGENT = 'Mozilla/5.0 (Linux; Android 11) OTT Navigator';
const PROBE_WORKERS = 64;
const PROBE_TIMEOUT_MS = 10_000;
const COUNTRY_CATEGORIES = ['Malaysia', 'Singapore', 'Hong Kong'];
const CATEGORIES = ['English', 'Chinese', 'Malay', 'Korean', 'Japanese', ...COUNTRY_CATEGORIES];
const CHINESE_DIALECTS = new Set(['Cantonese', 'Hakka', 'Hokkien', 'Teochew']);
const MALAY_LANGUAGES = new Set(['Malay', 'Indonesian']);
const MOVED_COUNTRIES = new Map([['hk', 'Hong Kong']]); // tvg-id country shown only in its own category, not its language
const HK_ID_KEYS = ['tvbjade', 'tvbclassic', 'tvbxinghe', 'phoenixchinese', 'rthk'];
const HK_NAME_RE =
  /\b(?:tvb jade|tvb classic|tvb xing he|jade|viu|hoy tv|rthk|mei ah|newsworld|news world|pet club|asian drama)\b|翡翠|明珠|無綫|无线|香港/i;

type Entry = { info: string; options: string[]; url: string };

const groupOf = (e: Entry) => /group-title="([^"]*)"/.exec(e.info)?.[1] ?? 'Undefined';
const countryOf = (e: Entry) => /tvg-id="[^"]*\.([a-z]{2})(?:@[^"]*)?"/.exec(e.info)?.[1] ?? '';
const nameOf = (e: Entry) => e.info.slice(e.info.lastIndexOf(',') + 1).trim();
const identOf = (e: Entry) => (/tvg-id="([^"]*)"/.exec(e.info)?.[1] ?? '').toLowerCase().split('@')[0];

function optionOf(e: Entry, key: string) {
  const prefix = `#EXTVLCOPT:http-${key}=`;
  return e.options.find((o) => o.startsWith(prefix))?.slice(prefix.length);
}

function withGroup(e: Entry, group: string) {
  const info = e.info.includes('group-title="')
    ? e.info.replace(/group-title="[^"]*"/, `group-title="${group}"`)
    : e.info.replace(/(#EXTINF:[^,\s]*)/, `$1 group-title="${group}"`);
  return { ...e, info, options: [...e.options] };
}

const toM3u = (e: Entry) => [e.info, ...e.options, e.url].join('\n');

async function main() {
  const entries = await collectEntries();
  const byUrl = new Map(entries.map((e) => [e.url, e]));
  console.log(`fetched ${entries.length} entries, ${byUrl.size} unique streams`);
  const verdictOf = await probeAll([...byUrl.values()]);
  const kept = entries.filter((e) => verdictOf.get(e.url) === 'alive');
  kept.sort((a, b) => CATEGORIES.indexOf(groupOf(a)) - CATEGORIES.indexOf(groupOf(b)) || cmp(nameOf(a).toLowerCase(), nameOf(b).toLowerCase()));
  writePlaylist(kept);
  const verdicts = [...verdictOf.values()];
  for (const verdict of ['alive', 'dead']) console.log(`${verdict}: ${verdicts.filter((v) => v === verdict).length} streams`);
  const groups = kept.map(groupOf);
  for (const category of CATEGORIES) console.log(`${category}: ${groups.filter((g) => g === category).length} channels`);
  console.log(`wrote ${kept.length} channel entries to ${OUTPUT}`);
}

const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

// Language entries regrouped into CATEGORIES, plus COUNTRY_CATEGORIES channels from country list
async function collectEntries() {
  const byLanguage = parseM3u(await fetchText(LANGUAGE_URL)).flatMap((entry) => {
    const category = categoryForEntry(entry);
    return category ? [withGroup(entry, category)] : [];
  });
  const byCountry = parseM3u(await fetchText(COUNTRY_URL)).filter((e) => COUNTRY_CATEGORIES.includes(groupOf(e)));
  return dedupe([...byLanguage, ...byCountry]);
}

function categoryForEntry(entry: Entry) {
  if (isHongKong(entry)) return 'Hong Kong';
  return MOVED_COUNTRIES.get(countryOf(entry)) ?? categoryForLanguage(groupOf(entry));
}

// Hong Kong channels go to Hong Kong category, whatever list they came from
function isHongKong(entry: Entry) {
  if (countryOf(entry) === 'hk') return true;
  const ident = identOf(entry);
  return HK_ID_KEYS.some((key) => ident.includes(key)) || HK_NAME_RE.test(nameOf(entry));
}

// Folds dialects into parent language; Indonesian counts as Malay. undefined = drop
function categoryForLanguage(language: string) {
  if (language.endsWith('English')) return 'English';
  if (language.endsWith('Chinese') || CHINESE_DIALECTS.has(language)) return 'Chinese';
  if (MALAY_LANGUAGES.has(language)) return 'Malay';
  if (language === 'Korean' || language === 'Japanese') return language;
  return undefined;
}

async function fetchText(url: string) {
  const res = await fetch(url, { headers: { 'User-Agent': DEFAULT_USER_AGENT }, signal: AbortSignal.timeout(60_000) });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.text();
}

function parseM3u(text: string) {
  const entries: Entry[] = [];
  let current: Entry | undefined;
  for (const line of text.split(/\r?\n/).map((raw) => raw.trim())) {
    if (line.startsWith('#EXTINF')) current = { info: line, options: [], url: '' };
    else if (current && line.startsWith('#')) current.options.push(line);
    else if (current && line) {
      current.url = line;
      entries.push(current);
      current = undefined;
    }
  }
  return entries;
}

// Drops repeats within one category; channel stays listed under each category it fits
function dedupe(entries: Entry[]) {
  return [...new Map(entries.map((e) => [`${groupOf(e)}\n${e.url}`, e])).values()];
}

async function probeAll(entries: Entry[]) {
  const verdicts = entries.map(() => 'dead');
  let next = 0;
  const worker = async () => {
    while (next < entries.length) {
      const i = next++;
      verdicts[i] = await probe(entries[i]);
    }
  };
  await Promise.all(Array.from({ length: PROBE_WORKERS }, worker));
  return new Map(entries.map((e, i) => [e.url, verdicts[i]]));
}

// alive or dead; 401/403 count as dead since they refuse Malaysian viewers too
async function probe(entry: Entry) {
  const referrer = optionOf(entry, 'referrer');
  const headers = { 'User-Agent': optionOf(entry, 'user-agent') ?? DEFAULT_USER_AGENT, ...(referrer ? { Referer: referrer } : {}) };
  try {
    const res = await fetch(entry.url, { headers, signal: AbortSignal.timeout(PROBE_TIMEOUT_MS) });
    if (!res.ok || !res.body) return 'dead';
    const head = await readHead(res.body, 512);
    return isPlayable(entry.url, head) ? 'alive' : 'dead';
  } catch {
    return 'dead';
  }
}

async function readHead(body: ReadableStream<Uint8Array>, limit: number) {
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (size < limit) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    size += value.length;
  }
  await reader.cancel().catch(() => {});
  return Buffer.concat(chunks).subarray(0, limit);
}

function isPlayable(url: string, head: Buffer) {
  if (url.toLowerCase().includes('.m3u8')) return head.toString('latin1').trimStart().startsWith('#EXTM3U');
  return head.length > 0;
}

function writePlaylist(entries: Entry[]) {
  mkdirSync(dirname(OUTPUT), { recursive: true });
  const body = entries.map(toM3u).join('\n');
  writeFileSync(OUTPUT, `#EXTM3U url-tvg="${GUIDE_URL}"\n${body}\n`, 'utf8');
}

await main();
