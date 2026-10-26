'use strict';

// Battery on Meta Ray-Ban Display: the glasses' and the band's battery now, when to charge, and how fast they drain.
//
// Where the numbers come from:
// - The glasses' level and charging come first from the glasses themselves (navigator.getBattery), whenever the browser
//   really knows them; that works with no link at all. The phone's report is the fallback.
// - The band's level and the history come from the readings the Tidal Glass phone app keeps (BatteryWatch: a line on
//   every change of either level, charging, wearing, the link, the glasses app in use, Follow my voice and Tidal
//   playing; kept 7 days). They are asked for over the phone's own sealed link: a `batHistory` on opening and every 5
//   minutes while on screen, after the first only for what is newer than the newest reading held; the answer comes back
//   on bkr/<topic>/bat. The last answer is kept on the glasses, so it shows at once next time, with its time, while the
//   link connects. The phone's retained state (gb, gc, bb) fills in the levels until the phone answers.
//
// Only what the phone can see is counted: wearing, charging, Follow my voice, which of Bakar's glasses apps is open, and
// Tidal playing. Meta's own apps, the display's brightness and the radios are not visible to it; a dim line under the
// list says so.

const BROKERS = [
  { url: 'wss://broker.emqx.io:8084/mqtt' },
  { url: 'wss://broker.hivemq.com:8884/mqtt' },
  { url: 'wss://public.cloud.shiftr.io', username: 'public', password: 'public' },
];
const MIN = 60000;
const HOUR = 60 * MIN;
const ASK_EVERY = 5 * MIN;
const ANSWER_WAIT = 12000;
const CALM_AFTER = 15000;
const LOW = 20;
const WINDOWS = { day: 24 * HOUR, week: 7 * 24 * HOUR };
const MAX = 1500; // readings per answer
const FRESH = 5 * MIN; // a state message older than this is not news from the phone
// The toolkit reports whole percents, so a fall of more than 2 points between two readings is time the phone did not
// see (a reconnect, a phone that was off). Assumes each whole percent is reported; not verified on the real glasses.
const JUMP = 2;
const LONG_GAP = 6 * HOUR; // two readings this far apart: a phone that was off
const ROW_POINTS = 3; // a row in the list needs 3 points of fall measured over 30 minutes
const ROW_HOURS = 0.5;
const STRETCH_TICKS = 3; // the time left needs 3 changes of the level in the current stretch off the charger
const BAND_FRESH = 30 * MIN; // a band level read longer ago than this says how old it is
const BAND_STALE = 45 * MIN; // a reading whose band level was read longer before it is not drawn
const GAP = 30 * MIN; // the chart's line breaks across time the phone could not vouch for, when longer than this
const $ = (id) => document.getElementById(id);

let topic = null;
let key = null;
let links = [];
let everLinked = false;
let asked = 0; // time stamp of the request this visit is waiting for (0: answered or none)
let request = null; // that request: { ts, since, incremental }
let askTimer = 0;
let tickTimer = 0;
let ageTimer = 0;
let hideTimer = 0;
let lastInputAt = Date.now();
let span = 'day';
let phone = null; // the phone's word on the levels: { g, gc, w, c, b, bt, bw, a, m, p, at, live }
let history = []; // readings, oldest first
let heldFrom = 0; // the readings held are complete from this time on (0: none held)
let heard = false; // the phone's readings are held (from an answer or the copy kept on the glasses)
let answeredAt = 0; // this visit's last answer, by the phone's clock (0: none yet)
let own = null; // the glasses' own battery, when the browser really knows it: { level: 0-100, charging }
let version = 0; // bumped whenever what the levels, list, time left and chart are drawn from changes
let drawn = '';
let listDrawn = '';
const recent = [];

// ---- pairing and crypto -------------------------------------------------------------------------

function fromB64url(s) {
  s = s.replace(/-/g, '+').replace(/_/g, '/');
  while (s.length % 4) s += '=';
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function toB64url(bytes) {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// On the glasses every installed app keeps its own storage, so the phone adds this one with the pairing in its
// address (#t=…&k=…, never sent to a server); a copy is kept for launches without it.
function readPairing() {
  const p = new URLSearchParams(location.hash.slice(1));
  let t = p.get('t');
  let k = p.get('k');
  try {
    if (t && k) localStorage.setItem('bg.pair', JSON.stringify({ t, k }));
    else {
      const saved = JSON.parse(localStorage.getItem('bg.pair') || 'null');
      if (saved) ({ t, k } = saved);
    }
  } catch (e) { /* storage may be off; the address still works */ }
  return t && k ? { t, k } : null;
}

async function seal(obj) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, new TextEncoder().encode(JSON.stringify(obj))));
  const out = new Uint8Array(12 + ct.length);
  out.set(iv, 0);
  out.set(ct, 12);
  return toB64url(out);
}

async function openMessage(text) {
  const buf = fromB64url(text.trim());
  const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: buf.slice(0, 12) }, key, buf.slice(12));
  return JSON.parse(new TextDecoder().decode(plain));
}

// ---- link -----------------------------------------------------------------------------------------
// All three brokers together, as the other glasses apps since 2026-10-25 (from home only one could be reached).

function linkUp() {
  return links.some((l) => l.connected);
}

function openLink() {
  if (links.length || typeof mqtt === 'undefined') return;
  const id = toB64url(crypto.getRandomValues(new Uint8Array(6))).replace(/[-_]/g, 'x');
  for (const { url, ...extra } of BROKERS) {
    const client = mqtt.connect(url, {
      clientId: `bgg-${id}-${links.length}`, clean: true, keepalive: 45, protocolVersion: 4,
      reconnectPeriod: 1500, connectTimeout: 8000, ...extra,
    });
    client.on('connect', () => {
      everLinked = true;
      client.subscribe([`bkr/${topic}/s`, `bkr/${topic}/bat`], { qos: 1 });
      // A broker that connects while a request is unanswered gets it too: the phone may be on that one only.
      if (asked) publishRequest(client);
      render();
    });
    // A broker hands over its stored copy of the phone's state on subscribing, marked retained; what the phone
    // publishes while the page listens arrives unmarked.
    client.on('message', (t, payload, packet) => handle(new TextDecoder().decode(payload), !!(packet && packet.retain)));
    client.on('close', render);
    client.on('error', () => { /* mqtt.js reconnects by itself */ });
    links.push(client);
  }
}

function closeLink() {
  const old = links;
  links = [];
  everLinked = false;
  for (const l of old) { try { l.end(true); } catch (e) { /* already closed */ } }
}

let requestBody = '';

async function ask() {
  const ts = Date.now();
  const from = ts - WINDOWS[span];
  const newest = history.length ? history[history.length - 1].at : 0;
  // Once the readings held reach back to the start of the span, only newer ones are asked for, and merged in. The
  // phone answers with readings at or after `since`, so the newest held comes back once and is dropped.
  const since = heldFrom && heldFrom <= from && newest >= from ? newest : from;
  asked = ts;
  request = { ts, since, incremental: since !== from };
  requestBody = await seal({ c: 'batHistory', ts, since, max: MAX });
  for (const l of links.filter((x) => x.connected)) publishRequest(l);
  render();
  schedule();
}

function publishRequest(client) {
  if (requestBody) client.publish(`bkr/${topic}/c`, requestBody, { qos: 1 });
}

async function handle(text, retained = false) {
  if (recent.includes(text)) return; // the same message from another broker
  recent.push(text);
  if (recent.length > 12) recent.shift();
  let m;
  try { m = await openMessage(text); } catch (e) { return; }
  if (m.t === 'state') onState(m, retained);
  else if (m.t === 'bat' && m.re === asked) onAnswer(m);
}

// The phone's state: the levels only (gb, gc, bb), until the phone answers. A broker's stored copy can be hours old, so
// its time is kept and shown, and it never replaces what the phone said on this visit.
function onState(m, retained) {
  if (retained && answeredAt) return;
  const at = typeof m.at === 'number' ? m.at : 0;
  if (phone && at <= phone.at) return; // not newer than what is held
  const charging = m.gc === true;
  let g = typeof m.gb === 'number' && m.gb >= 0 ? m.gb : -1;
  // The toolkit's 0 before the link settles is not known yet; gc is only ever sent while charging.
  if (g === 0 && !charging) g = -1;
  const b = typeof m.bb === 'number' && m.bb >= 0 ? m.bb : -1;
  const prev = phone || {};
  phone = {
    ...prev,
    g,
    gc: charging ? 'CHARGING' : g >= 0 ? 'NOT_CHARGING' : '',
    b,
    bt: b >= 0 && b === prev.b ? prev.bt : 0, // the state does not say when the band was read
    bw: b < 0 ? prev.bw : undefined,
    p: typeof m.play === 'boolean' ? m.play : prev.p,
    at: at || Date.now(),
    live: !retained && Date.now() - at < FRESH,
  };
  version++;
  render();
}

function onAnswer(m) {
  const req = request && request.ts === m.re ? request : null;
  asked = 0;
  request = null;
  answeredAt = typeof m.at === 'number' ? m.at : Date.now();
  phone = { ...settle(m.now && typeof m.now === 'object' ? m.now : {}), at: answeredAt, live: true };
  const h = Array.isArray(m.h) ? m.h.filter((r) => r && typeof r.at === 'number').map(settle) : [];
  h.sort((x, y) => x.at - y.at);
  const capped = h.length >= MAX;
  if (req && req.incremental && !capped && (!h.length || h[0].at >= req.since)) {
    const newest = history.length ? history[history.length - 1].at : -Infinity;
    history = history.concat(h.filter((r) => r.at > newest));
  } else {
    history = h;
    // An answer to a full request holds everything since the request's start, unless it hit the most a message
    // carries; then, or when the request is not known, it holds only from its oldest reading.
    heldFrom = req && !req.incremental && !capped ? req.since : h.length ? h[0].at : answeredAt;
  }
  const keepFrom = Date.now() - WINDOWS.week; // a week at most, as on the phone
  if (history.length && history[0].at < keepFrom) {
    history = history.filter((r) => r.at >= keepFrom);
    heldFrom = Math.max(heldFrom, keepFrom);
  }
  heard = true;
  save();
  version++;
  render();
  schedule();
}

// ---- the copy kept on the glasses ---------------------------------------------------------------------
// The last answer (levels, band and readings), drawn at once on the next launch, marked with its time, while the link
// connects. Per pairing; storage may be off, and then nothing is kept.

function savedKey() {
  return `bg.last.${topic}`;
}

function save() {
  try {
    const { live, at, ...now } = phone || {};
    localStorage.setItem(savedKey(), JSON.stringify({ at: answeredAt, now, h: history, heldFrom }));
  } catch (e) { /* full or off: the next answer tries again */ }
}

function loadSaved() {
  try {
    const s = JSON.parse(localStorage.getItem(savedKey()) || 'null');
    if (!s || typeof s.at !== 'number' || !Array.isArray(s.h)) return;
    phone = { ...settle(s.now && typeof s.now === 'object' ? s.now : {}), at: s.at, live: false };
    history = s.h.filter((r) => r && typeof r.at === 'number').map(settle);
    heldFrom = typeof s.heldFrom === 'number' ? s.heldFrom : 0;
    heard = true;
    version++;
  } catch (e) { /* nothing usable kept */ }
}

// ---- the glasses' own battery ---------------------------------------------------------------------------
// Chromium answers "full, charging, never empties" when it has no battery data at all; that exact answer is no data
// (as in Quran Glass; a truly full battery on its charger looks the same, and the phone's report then fills in).

function watchOwnBattery() {
  if (!navigator.getBattery) return;
  navigator.getBattery().then((b) => {
    const read = () => {
      const unknown = b.level === 1 && b.charging && b.chargingTime === 0 && b.dischargingTime === Infinity;
      const next = unknown ? null : { level: Math.round(b.level * 100), charging: !!b.charging };
      if (JSON.stringify(next) === JSON.stringify(own)) return;
      own = next;
      version++;
      render();
    };
    read();
    for (const ev of ['levelchange', 'chargingchange', 'chargingtimechange', 'dischargingtimechange']) b.addEventListener(ev, read);
  }).catch(() => { /* no battery to read: the phone's report stands */ });
}

// ---- what the readings say ---------------------------------------------------------------------------

// Meta's toolkit reports the glasses at 0% with charging unknown until the link settles: that is "not known yet",
// not an empty battery (it drew a drop to 0% on the real phone's chart, 2026-10-25 18:55).
function settle(r) {
  return r.g === 0 && r.gc !== 'CHARGING' && r.gc !== 'NOT_CHARGING' ? { ...r, g: -1 } : r;
}

const PLACES = {
  notworn: 'Not worn',
  voice: 'Follow my voice (glasses mic on)',
  tidal: 'Tidal Glass open',
  sound: 'Sound Lab open',
  music: 'Music playing',
  worn: 'Worn, anything else',
};
// What a time worked out from one situation's rate depends on.
const IF_SAME = {
  notworn: 'if you leave them off',
  voice: 'if Follow my voice stays on',
  tidal: 'if Tidal Glass stays open',
  sound: 'if Sound Lab stays open',
  music: 'if the music keeps playing',
  worn: 'if you keep them on',
};
// Why the phone cannot read the band, each short enough for the one line under it, so nothing below moves.
const BAND_WHY = {
  'no-permission': 'Phone app needs Bluetooth access',
  'not-paired': 'Not paired with your phone',
  'no-battery-service': "Doesn't share its battery level",
  'out-of-range': "Out of your phone's range",
};

// Quran Glass talks to the phone only during Follow my voice, so its "in use" is folded into Follow my voice.
function situation(r) {
  if (r.w === 'DOFFED') return 'notworn';
  if (r.w !== 'DONNED') return null;
  if (r.m || r.a === 'quran') return 'voice';
  if (r.a === 'tidal') return 'tidal';
  if (r.a === 'sound') return 'sound';
  if (r.p) return 'music';
  return 'worn';
}

// A reading counts for drain only when the phone saw the glasses and knew they were not charging and whether they
// were worn; for charging, when it saw them charging.
function drainLevel(r) {
  return r.c !== false && r.gc === 'NOT_CHARGING' && (r.w === 'DONNED' || r.w === 'DOFFED') && r.g >= 0 ? r.g : -1;
}

function chargeLevel(r) {
  return r.c !== false && r.gc === 'CHARGING' && r.g >= 0 ? r.g : -1;
}

// Tick to tick. A level moves in whole percents, so a rate is measured from one change of it to another, inside one
// unbroken stretch: a reading the phone could not vouch for, a jump of more than 2 points, a step the wrong way or a gap
// over 6 hours ends the stretch, and the next change only starts a new one. The time between two changes counts for a
// group only when that group held all the way between them. `quiet` is the longest time a group held with no change at
// all (less than a point fell in it), for "under X% an hour"; `fell` says some change happened while it held.
function measure(readings, levelOf, groupOf, rising = false, timeOf = (r) => r.at) {
  const groups = {};
  const slot = (k) => groups[k] || (groups[k] = { points: 0, hours: 0, quiet: 0, fell: false });
  let prev = null; // the last reading of this stretch: { at, level, group }
  let first = null; // the first change of this stretch
  let tick = null; // its last change
  let ticks = 0;
  let held = null; // the groups seen since the last change
  let still = null; // since when this level and this group have held: { at, group }
  const endQuiet = (at) => {
    if (still && at > still.at) {
      const o = slot(still.group);
      o.quiet = Math.max(o.quiet, (at - still.at) / HOUR);
    }
    still = null;
  };
  const stop = (at) => {
    endQuiet(at);
    prev = first = tick = held = null;
    ticks = 0;
  };
  for (const r of readings) {
    const level = levelOf(r);
    const at = timeOf(r);
    const group = level >= 0 ? groupOf(r) : null;
    if (group == null) {
      if (prev) stop(at); // the level held until the phone stopped vouching for it
      continue;
    }
    if (prev) {
      const step = rising ? level - prev.level : prev.level - level;
      if (step < 0 || step > JUMP || at - prev.at > LONG_GAP) stop(prev.at);
    }
    if (!prev) {
      prev = { at, level, group };
      held = new Set([group]);
      still = { at, group };
      continue;
    }
    if (level !== prev.level) {
      endQuiet(at);
      for (const k of held) slot(k).fell = true;
      if (tick && held.size === 1) {
        const [only] = held;
        const o = slot(only);
        o.points += Math.abs(tick.level - level);
        o.hours += (at - tick.at) / HOUR;
      }
      tick = { at, level };
      if (!first) first = tick;
      ticks++;
      held = new Set([group]);
      still = { at, group };
    } else {
      held.add(group);
      if (group !== still.group) {
        endQuiet(at);
        still = { at, group };
      }
    }
    prev = { at, level, group };
  }
  if (prev) endQuiet(prev.at);
  return { groups, end: prev && { level: prev.level, at: prev.at, first, tick, ticks } };
}

// Points per hour from the first change of the last stretch to its last change.
function stretchRate(end) {
  if (!end || !end.first || end.ticks < STRETCH_TICKS) return 0;
  const points = Math.abs(end.first.level - end.tick.level);
  const hours = (end.tick.at - end.first.at) / HOUR;
  return points > 0 && hours > 0 ? points / hours : 0;
}

// The phone's word now as one more reading at its time, when it shows nothing new: it says the last state held until
// then. (When it differs, the change happened at a time the readings do not tell.)
function withNow(readings, same) {
  const last = readings[readings.length - 1];
  if (!last || !phone || !phone.live || !(phone.at > last.at)) return readings;
  const now = { ...last };
  for (const k of ['g', 'gc', 'w', 'c', 'b', 'bt', 'a', 'm', 'p']) if (phone[k] !== undefined) now[k] = phone[k];
  now.at = phone.at;
  return same(now, last) ? readings.concat([now]) : readings;
}

function rows(readings) {
  const pts = withNow(readings, (n, l) => n.g === l.g && n.gc === l.gc && n.c === l.c && situation(n) === situation(l));
  const { groups } = measure(pts, drainLevel, situation);
  const out = [];
  for (const [s, o] of Object.entries(groups)) {
    if (!PLACES[s]) continue;
    if (o.points >= ROW_POINTS && o.hours >= ROW_HOURS) {
      const rate = o.points / o.hours;
      out.push({ s, sort: rate, rate: perHour(rate), over: duration(o.hours) });
    } else if (!o.fell && o.quiet >= ROW_HOURS) {
      const x = Math.ceil(1 / o.quiet);
      out.push({ s, sort: x / 2, rate: `under ${x}% an hour`, over: duration(o.quiet) });
    }
  }
  return out.sort((a, b) => b.sort - a.sort);
}

function perHour(rate) {
  const n = Math.round(rate);
  return n >= 1 ? `${n}% an hour` : 'under 1% an hour';
}

// The band, by the time it was read (bt), so a band the phone has stopped reading adds no hours.
function bandRate(readings) {
  const pts = withNow(readings, (n, l) => n.b === l.b);
  const { groups } = measure(pts, (r) => (r.b >= 0 ? r.b : -1), () => 'band', false, (r) => (r.bt > 0 ? r.bt : r.at));
  const o = groups.band;
  if (!o) return '';
  if (o.points >= ROW_POINTS && o.hours >= ROW_HOURS) return `Uses ${o.points / o.hours >= 0.5 ? 'about ' : ''}${perHour(o.points / o.hours)}`;
  if (!o.fell && o.quiet >= ROW_HOURS) return `Uses under ${Math.ceil(1 / o.quiet)}% an hour`;
  return '';
}

// The glasses' level shown: the glasses' own reading first, else the phone's.
function glassesShown(stale) {
  if (own) return { value: own.level, charging: own.charging, old: false };
  if (phone && phone.g >= 0) return { value: phone.g, charging: phone.gc === 'CHARGING', old: stale || phone.c === false };
  return { value: -1, charging: !!phone && phone.gc === 'CHARGING', old: false };
}

// What is going on now, for a time worked out from one situation's rate: the phone's word, else the last reading.
function current() {
  const last = history.length ? history[history.length - 1] : {};
  const p = phone || {};
  const out = {};
  for (const k of ['w', 'a', 'm', 'p']) out[k] = p[k] !== undefined ? p[k] : last[k];
  return out;
}

// When the phone app lost the glasses: the first reading of the run without them at the end of the readings.
function lostAt() {
  let at = 0;
  for (let i = history.length - 1; i >= 0 && history[i].c === false; i--) at = history[i].at;
  return at;
}

// The line under the levels: when to charge, said as a clock time.
// - Charging: when they will be full, from this charge's own readings.
// - Otherwise from the current stretch off the charger, from one change of the level to another (3 changes at least);
//   it starts after the last reading that was charging, unknown or without the glasses, so a top-up or a reconnect
//   starts it again.
// - Until then, today's rate for what is going on now, said as such.
function timeLine(G) {
  const none = { text: '' };
  if (!(G.value >= 0)) return none;
  if (G.charging) {
    if (G.value >= 100) return none;
    const { end } = measure(history, chargeLevel, () => 'charging', true);
    const rate = stretchRate(end);
    if (!rate) return none;
    const full = end.tick.at + ((100 - end.tick.level) / rate) * HOUR;
    return full > Date.now() ? { text: `Full around ${when(roundTime(full))}`, full } : none;
  }
  if (phone && phone.c === false) {
    const lost = lostAt();
    return { text: lost ? `Phone app lost the glasses at ${when(lost)}` : 'Phone app lost the glasses' };
  }
  const { end } = measure(history, drainLevel, () => 'off');
  const rate = stretchRate(end);
  if (rate) {
    if (G.value > end.tick.level + 1) return none; // higher than the phone's last reading: charged since
    // From the last change the level falls at the stretch's rate. A change overdue means it falls slower now, so it is
    // taken as just about to change; the glasses' own reading, when it is ahead of the phone's, starts from now.
    let at = Math.max(end.tick.at, Date.now() - HOUR / rate);
    let level = end.tick.level;
    if (G.value < level) {
      at = Date.now();
      level = G.value;
    }
    return targets(level, at, rate, '');
  }
  const s = situation(current());
  const day = s && measure(withinDay(), drainLevel, situation).groups[s];
  if (day && day.points >= ROW_POINTS && day.hours >= ROW_HOURS) return targets(G.value, Date.now(), day.points / day.hours, IF_SAME[s]);
  return none;
}

function withinDay() {
  const from = Date.now() - WINDOWS.day;
  return history.filter((r) => r.at >= from);
}

function targets(level, at, rate, condition) {
  const low = at + ((level - LOW) / rate) * HOUR;
  const empty = at + (level / rate) * HOUR;
  if (level <= LOW) return { text: `Empty around ${when(roundTime(empty))}${condition ? ` ${condition}` : ''}`, empty };
  if (condition) return { text: `${LOW}% around ${when(roundTime(low))} ${condition}`, low, empty };
  return { text: `${LOW}% around ${when(roundTime(low))} · empty around ${when(roundTime(empty))}`, low, empty };
}

// A clock time no finer than the data: to 5 minutes within the hour, 15 minutes within 3 hours, 30 minutes beyond.
function roundTime(ms) {
  const ahead = ms - Date.now();
  const step = ahead < HOUR ? 5 : ahead <= 3 * HOUR ? 15 : 30;
  const d = new Date(ms);
  const mins = d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60;
  d.setHours(0, Math.round(mins / step) * step, 0, 0);
  return d.getTime();
}

function duration(hours) {
  const mins = Math.round(hours * 60);
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

function ago(ms) {
  const mins = Math.round(ms / MIN);
  if (mins < 60) return `${mins} min ago`;
  const h = Math.round(ms / HOUR);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

function clock(ms) {
  return new Date(ms).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

// A clock time, with the day when it is neither today nor within 12 hours.
function when(ms) {
  if (!ms) return 'earlier';
  const d = new Date(ms);
  if (d.toDateString() === new Date().toDateString() || Math.abs(ms - Date.now()) < 12 * HOUR) return clock(ms);
  return `${d.toLocaleDateString([], { weekday: 'short' })} ${clock(ms)}`;
}

// ---- drawing ----------------------------------------------------------------------------------------
// The top line follows the link and the clock; the levels, the list, the time left and the chart are drawn again only
// when what they come from changes (an answer, a state, the glasses' own battery, the span). The DOM is touched only
// where something differs.

function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}

function setAttr(el, name, value) {
  if (value == null) {
    if (el.hasAttribute(name)) el.removeAttribute(name);
  } else if (el.getAttribute(name) !== value) el.setAttribute(name, value);
}

function setHidden(el, hidden) {
  if (el.hidden !== hidden) el.hidden = hidden;
}

function notAnswering() {
  return !!asked && Date.now() - asked > ANSWER_WAIT;
}

// The phone's word is its last word, not news: from an earlier visit, a broker's copy, or a phone not answering now.
function phoneStale() {
  return !phone || !phone.live || notAnswering() || !linkUp();
}

function render() {
  const paired = !!key;
  setHidden($('unpaired'), paired);
  setHidden($('main'), !paired);
  if (!paired) return;
  renderTop();
  const sig = `${version}|${span}|${linkUp()}|${!!asked}|${notAnswering()}`;
  if (sig === drawn) return;
  drawn = sig;
  renderData();
}

// The top line: where the numbers come from, and when the phone's levels are from when they are not news.
function renderTop() {
  const waiting = notAnswering();
  let text = !linkUp() ? (everLinked ? 'Reconnecting…' : 'Connecting to your phone…')
    : waiting ? 'Your phone is not answering'
    : answeredAt ? `From your phone · ${when(answeredAt)}` : 'Asking your phone…';
  if (phone && phoneStale()) {
    const g = !own && phone.g >= 0;
    const b = phone.b >= 0;
    if (g || b) text += ` · ${g && b ? 'levels' : g ? 'glasses' : 'band'} from ${when(phone.at)}`;
  }
  setText($('stateText'), text);
  $('state').classList.toggle('warn', waiting);
  $('main').classList.toggle('stale', !!phone && phoneStale());
}

function renderData() {
  const stale = phoneStale();
  const G = glassesShown(stale);
  level($('glasses'), G.value, G.charging, G.old);
  const band = phone && phone.b >= 0 ? phone.b : -1;
  const bandAge = band >= 0 && phone.bt > 0 ? Date.now() - phone.bt : 0;
  const bandOld = band >= 0 && (stale || bandAge > BAND_FRESH);
  level($('band'), band, false, bandOld);
  setText($('glassesNote'), G.charging ? 'Charging' : '');

  const windowMs = WINDOWS[span];
  const inWindow = history.filter((r) => r.at >= Date.now() - windowMs);
  let note = '';
  if (phone && band < 0) note = BAND_WHY[phone.bw] || (heard ? 'Not read yet' : '');
  else if (bandAge > BAND_FRESH) note = `Read ${ago(bandAge)}`;
  else if (band >= 0) note = bandRate(inWindow);
  setText($('bandNote'), note);
  // A "read … ago" moves with the clock; it alone is drawn again each minute while shown.
  clearTimeout(ageTimer);
  if (bandAge > BAND_FRESH && document.visibilityState === 'visible') ageTimer = setTimeout(() => { version++; render(); }, MIN);

  const t = timeLine(G);
  const left = $('left');
  setText(left, t.text);
  setAttr(left, 'data-low', t.low ? String(Math.round(t.low)) : null);
  setAttr(left, 'data-empty', t.empty ? String(Math.round(t.empty)) : null);
  setAttr(left, 'data-full', t.full ? String(Math.round(t.full)) : null);

  // Why the list is empty, said once: no link (the top line says it), the phone not heard from yet, or too little.
  const list = heard ? rows(inWindow) : [];
  renderList(list, heard ? 'Not enough readings yet: about 30 minutes off the charger'
    : linkUp() && asked ? "Waiting for your phone's readings" : '');
  renderChart(inWindow, windowMs, G, bandOld);
  setText($('span'), span === 'day' ? 'Last 24 hours' : 'Last 7 days');
  setAttr($('span'), 'aria-label', span === 'day' ? 'Showing the last 24 hours. Press for 7 days' : 'Showing the last 7 days. Press for 24 hours');
}

function level(el, value, charging, old) {
  const known = value != null && value >= 0;
  setText(el.querySelector('.pct'), known ? `${value}%` : '—');
  el.classList.toggle('unknown', !known);
  el.classList.toggle('low', known && value <= LOW && !charging);
  el.classList.toggle('charging', !!charging);
  el.classList.toggle('old', known && !!old);
  const fill = el.querySelector('.fill');
  const size = known ? `${Math.max(2, value)}%` : '0%';
  if (fill.style.inlineSize !== size) fill.style.inlineSize = size;
}

function renderList(list, empty) {
  setHidden($('notCounted'), !list.length);
  const sig = list.length ? JSON.stringify(list) : `-${empty}`;
  if (sig === listDrawn) return;
  listDrawn = sig;
  const ul = $('usesGlasses');
  ul.replaceChildren();
  const add = (parent, tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    el.textContent = text;
    parent.append(el);
    return el;
  };
  if (!list.length) {
    if (empty) add(ul, 'li', 'empty', empty);
    return;
  }
  for (const r of list) {
    const li = add(ul, 'li', '', '');
    add(li, 'span', 'what', PLACES[r.s]);
    add(li, 'span', 'over', `over ${r.over}`);
    add(li, 'span', 'rate', r.rate);
  }
}

// One line per device; it breaks where the readings stop being continuous: a level unknown or without the glasses for
// over 30 minutes, or a jump of more than 2 points (an overnight gap with the phone off). The phone writes a reading
// only when something changes, so a long time with no reading and no jump is a level that held, and stays joined.
function linePath(readings, valueOf, current, t0, end, xOf, yOf) {
  const segs = [];
  let seg = null;
  let last = null;
  let unseen = false;
  const add = (at, v) => {
    if (last && (Math.abs(v - last.v) > JUMP || (unseen && at - last.at > GAP))) seg = null;
    if (!seg) segs.push((seg = []));
    seg.push([xOf(at), yOf(v)]);
    last = { at, v };
    unseen = false;
  };
  for (const r of readings) {
    if (r.at < t0) continue;
    const v = valueOf(r);
    if (v >= 0) add(r.at, v);
    else unseen = true;
  }
  if (current != null) add(end, current); // carried to now, at the level shown above
  return segs.map((s) => {
    if (s.length === 1) s.unshift([Math.max(0, s[0][0] - 4), s[0][1]]); // a reading alone: a short tick
    return `M${s.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join('L')}`;
  }).join('');
}

function renderChart(readings, windowMs, G, bandOld) {
  const w = 540;
  const h = 96;
  const end = Date.now();
  // The axis starts at the first reading when there is less than the whole span, so a short history fills it.
  const first = readings.length ? readings[0].at : end - windowMs;
  const t0 = Math.max(end - windowMs, Math.min(first, end - 30 * MIN));
  const xOf = (at) => ((at - t0) / (end - t0)) * w;
  const yOf = (v) => h - (v / 100) * (h - 2) - 1;
  // Each line runs to now only when the level above is news.
  const gNow = G.value >= 0 && !G.old ? G.value : null;
  const bNow = phone && phone.b >= 0 && !bandOld ? phone.b : null;
  setAttr($('lineGlasses'), 'd', linePath(readings, (r) => (r.c === false ? -1 : r.g), gNow, t0, end, xOf, yOf));
  setAttr($('lineBand'), 'd', linePath(readings, (r) => (r.b >= 0 && !(r.bt > 0 && r.at - r.bt > BAND_STALE) ? r.b : -1), bNow, t0, end, xOf, yOf));
  // When less than the span is held (a short history, or an answer cut at the most a message carries), the left label
  // says when the readings start, with the day when it is not today.
  setText($('since'), readings.length && t0 > end - windowMs ? when(t0) : (span === 'day' ? '24 h ago' : '7 days ago'));
  setAttr($('chart'), 'aria-label', `Battery levels over the ${span === 'day' ? 'last 24 hours' : 'last 7 days'}`);
}

// ---- visits and timers --------------------------------------------------------------------------------
// No steady tick: one timeout for the moment the phone counts as not answering, one for calm, and none while calm with
// nothing asked.

function schedule() {
  clearTimeout(tickTimer);
  tickTimer = 0;
  if (!key || document.visibilityState !== 'visible') return;
  const now = Date.now();
  const due = [];
  if (asked && now - asked <= ANSWER_WAIT) due.push(asked + ANSWER_WAIT + 20);
  if (!document.body.classList.contains('calm')) due.push(lastInputAt + CALM_AFTER + 20);
  if (!due.length) return;
  tickTimer = setTimeout(() => {
    tickTimer = 0;
    checkCalm();
    render();
    schedule();
  }, Math.max(20, Math.min(...due) - now));
}

function startVisit() {
  if (!key) return;
  clearTimeout(hideTimer);
  openLink();
  ask();
  clearInterval(askTimer);
  askTimer = setInterval(ask, ASK_EVERY);
  schedule();
}

// Battery: off screen, the link closes and the timers stop. Coming back is a new visit: what the phone said before is
// its last word until it answers again.
function onVisibility() {
  if (document.visibilityState === 'visible') startVisit();
  else {
    clearInterval(askTimer);
    clearTimeout(tickTimer);
    clearTimeout(ageTimer);
    asked = 0;
    request = null;
    answeredAt = 0;
    if (phone) phone = { ...phone, live: false };
    version++;
    hideTimer = setTimeout(() => { if (document.visibilityState !== 'visible') closeLink(); }, 400);
  }
}

function wakeUp() {
  lastInputAt = Date.now();
  document.body.classList.remove('calm');
  schedule();
}

function checkCalm() {
  document.body.classList.toggle('calm', Date.now() - lastInputAt > CALM_AFTER);
}

async function main() {
  const pair = readPairing();
  if (pair) {
    topic = pair.t;
    try { key = await crypto.subtle.importKey('raw', fromB64url(pair.k), 'AES-GCM', false, ['encrypt', 'decrypt']); } catch (e) { key = null; }
  }
  document.addEventListener('keydown', wakeUp, true);
  document.addEventListener('pointerdown', wakeUp, true);
  document.addEventListener('click', wakeUp, true);
  document.addEventListener('visibilitychange', onVisibility);
  $('span').addEventListener('click', () => { span = span === 'day' ? 'week' : 'day'; version++; render(); if (key) ask(); });
  window.addEventListener('online', () => { if (document.visibilityState === 'visible') { closeLink(); openLink(); ask(); } });
  if ('serviceWorker' in navigator) { try { navigator.serviceWorker.register('sw.js').catch(() => {}); } catch (e) { /* optional */ } }
  if (key) {
    loadSaved();
    watchOwnBattery();
  }
  render();
  if (key) {
    $('span').focus();
    if (document.visibilityState === 'visible') startVisit();
  }
}

main();
