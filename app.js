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
const PACE = 30 * MIN; // "At this pace": 3 or more of those changes since the situation last changed, in the last 30 minutes,
const PACE_SPAN = 5 * MIN; // spanning 5 minutes at least, so three quick changes do not set the pace alone
const BLIP = MIN; // an off-the-face spell shorter than this between two worn readings is taken for the wear sensor
const LEVEL_FRESH = 30 * MIN; // no time is worked out from a glasses level the phone vouched for longer ago than this
const CHARGED = 80; // while charging, a time is given up to this level only (a charge slows near full, unmeasured here)
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
let levelTimer = 0;
let hideTimer = 0;
let downAt = 0; // since when no broker has carried the link on this visit (0: one does, or the link is closed)
let lastInputAt = Date.now();
let span = 'day';
let phone = null; // the phone's word on the levels: { g, gc, w, c, b, bt, bw, a, m, p, at, live }
let history = []; // readings, oldest first
let heldFrom = 0; // the readings held are complete from this time on (0: none held)
let heldAll = false; // the last full answer was cut at the most one answer carries: no older readings can be had
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

// Retries are scheduled here, not by mqtt.js (reconnectPeriod 0), as in Tidal Glass build 17. Its link report of
// 2026-10-25 20:08 showed a page left open without signal trying every broker about every 2 s for 8.5 minutes (205
// tries to shiftr alone), which keeps the radio awake. Now: 1 s for the first three tries, then doubling to 30 s while
// nothing is connected; a broker that keeps failing while another carries the link backs off to 10 min. A connect, a
// network change ('online') or a visit that opens the link again starts it fast again (each opens fresh links). The
// link closes 400 ms after the page is hidden, and closing it clears every pending retry, so from then on nothing is
// tried (a retry already due in those 400 ms can still go out).
function retryDelay(fails, othersUp) {
  if (othersUp) return Math.min(600000, 1000 * 2 ** Math.min(fails, 10));
  return fails <= 3 ? 1000 : Math.min(30000, 1000 * 2 ** (fails - 3));
}

function scheduleRetry(client) {
  if (client.retryTimer || !links.includes(client) || client.connected) return;
  client.fails = (client.fails || 0) + 1;
  client.retryTimer = setTimeout(() => {
    client.retryTimer = 0;
    if (!links.includes(client) || client.connected) return;
    try { client.reconnect(); } catch (e) { scheduleRetry(client); }
  }, retryDelay(client.fails, linkUp()));
}

// Connect timeout 6 s (was 8 s), as Tidal Glass: the slowest successful connect the glasses reported was 4.1 s, and
// every try that hangs costs the whole timeout before the next one.
function openLink() {
  if (links.length || typeof mqtt === 'undefined') return;
  downAt = Date.now();
  const id = toB64url(crypto.getRandomValues(new Uint8Array(6))).replace(/[-_]/g, 'x');
  for (const { url, ...extra } of BROKERS) {
    const client = mqtt.connect(url, {
      clientId: `bgg-${id}-${links.length}`, clean: true, keepalive: 45, protocolVersion: 4,
      reconnectPeriod: 0, connectTimeout: 6000, ...extra,
    });
    client.on('connect', () => {
      client.fails = 0;
      clearTimeout(client.retryTimer);
      client.retryTimer = 0;
      everLinked = true;
      downAt = 0;
      client.subscribe([`bkr/${topic}/s`, `bkr/${topic}/bat`], { qos: 1 });
      // A broker that connects while a request is unanswered gets it too: the phone may be on that one only.
      if (asked) publishRequest(client);
      render();
    });
    // A broker hands over its stored copy of the phone's state on subscribing, marked retained; what the phone
    // publishes while the page listens arrives unmarked.
    client.on('message', (t, payload, packet) => handle(new TextDecoder().decode(payload), !!(packet && packet.retain)));
    client.on('close', () => {
      if (!linkUp() && !downAt && links.includes(client)) downAt = Date.now();
      render();
      schedule();
      scheduleRetry(client);
    });
    client.on('error', () => { /* a failed try ends in 'close', which schedules the next one */ });
    links.push(client);
  }
}

function closeLink() {
  const old = links;
  links = [];
  everLinked = false;
  downAt = 0;
  for (const l of old) { clearTimeout(l.retryTimer); try { l.end(true); } catch (e) { /* already closed */ } }
}

let requestBody = '';

async function ask() {
  const ts = Date.now();
  const from = ts - WINDOWS[span];
  const newest = history.length ? history[history.length - 1].at : 0;
  // Once the readings held reach back to the start of the span (or are all one answer can carry), only newer ones are
  // asked for, and merged in. The phone answers with readings at or after `since`, so the newest held comes back once
  // and is dropped.
  const since = (heldAll || (heldFrom && heldFrom <= from)) && newest >= from ? newest : from;
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
    heldAll = capped;
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
    localStorage.setItem(savedKey(), JSON.stringify({ at: answeredAt, now, h: history, heldFrom, heldAll }));
  } catch (e) { /* full or off: the next answer tries again */ }
}

function loadSaved() {
  try {
    const s = JSON.parse(localStorage.getItem(savedKey()) || 'null');
    if (!s || typeof s.at !== 'number' || !Array.isArray(s.h)) return;
    phone = { ...settle(s.now && typeof s.now === 'object' ? s.now : {}), at: s.at, live: false };
    history = s.h.filter((r) => r && typeof r.at === 'number').map(settle);
    heldFrom = typeof s.heldFrom === 'number' ? s.heldFrom : 0;
    heldAll = s.heldAll === true;
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

// "Tidal playing" is Tidal's own playing state on the phone (p); other apps' audio is not seen, so it is not "music"
// (Director recheck 1, E). "Follow my voice" names no microphone: the phone listens through the glasses' microphone
// when it can reach it, and through its own otherwise, and a reading does not say which.
const PLACES = {
  notworn: 'Not worn',
  voice: 'Follow my voice',
  tidal: 'Tidal Glass open',
  sound: 'Sound Lab open',
  music: 'Tidal playing',
  worn: 'Worn, anything else',
};
// What a time worked out from one situation's rate depends on.
const IF_SAME = {
  notworn: 'if you leave them off',
  voice: 'if Follow my voice stays on',
  tidal: 'if Tidal Glass stays open',
  sound: 'if Sound Lab stays open',
  music: 'if Tidal keeps playing',
  worn: 'if you keep them on',
};
// Why the phone cannot read the band, each short enough for the one line under it, so nothing below moves. The phone
// says "out-of-range" when it tried to read a band that did not answer: out of range, or off, or flat, so the words
// name no cause. Phone builds without the "bt-off" and "not-read-yet" codes also said it with Bluetooth off and before
// the first read, which the words cover too.
const BAND_WHY = {
  'no-permission': 'Phone app needs Bluetooth access',
  'not-paired': 'Not paired with your phone',
  'no-battery-service': "Doesn't share its battery level",
  'out-of-range': "Your phone can't reach it",
  'bt-off': 'Bluetooth is off on your phone',
  'not-read-yet': 'Not read yet',
};

// Follow my voice counts while the phone says it is listening (m); the phone ends m when Follow my voice stops. Quran
// Glass's own "in use" lasts 4 minutes after its last message, so it is not folded in: minutes after Follow my voice
// stopped would count as listening. Quran Glass open without Follow my voice falls under what else is going on (the
// note under the list says it is not counted).
function situation(r) {
  if (r.w === 'DOFFED') return 'notworn';
  if (r.w !== 'DONNED') return null;
  if (r.m) return 'voice';
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
// all (less than a point fell in it), for "under X% an hour"; `fell` says some change happened while it held. The last
// stretch comes back as `end`: its last reading, the index of its first reading (`from`) and its changes, oldest first.
function measure(readings, levelOf, groupOf, rising = false, timeOf = (r) => r.at) {
  const groups = {};
  const slot = (k) => groups[k] || (groups[k] = { points: 0, hours: 0, quiet: 0, fell: false });
  let prev = null; // the last reading of this stretch: { at, level, group }
  let from = -1; // the index of its first reading
  let changes = []; // its changes of the level: { at, level }
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
    prev = held = null;
    from = -1;
    changes = [];
  };
  readings.forEach((r, i) => {
    const level = levelOf(r);
    const at = timeOf(r);
    const group = level >= 0 ? groupOf(r) : null;
    if (group == null) {
      if (prev) stop(at); // the level held until the phone stopped vouching for it
      return;
    }
    if (prev) {
      const step = rising ? level - prev.level : prev.level - level;
      if (step < 0 || step > JUMP) {
        // The level moved where the phone did not see it, so the time it seemed to hold before is not vouched for:
        // that quiet span is dropped, not counted as "under X% an hour".
        still = null;
        stop(prev.at);
      } else if (at - prev.at > LONG_GAP) stop(prev.at); // a phone that was off: what it saw until then stands
    }
    if (!prev) {
      prev = { at, level, group };
      from = i;
      held = new Set([group]);
      still = { at, group };
      return;
    }
    if (level !== prev.level) {
      endQuiet(at);
      for (const k of held) slot(k).fell = true;
      const tick = changes[changes.length - 1];
      if (tick && held.size === 1) {
        const [only] = held;
        const o = slot(only);
        o.points += Math.abs(tick.level - level);
        o.hours += (at - tick.at) / HOUR;
      }
      changes.push({ at, level });
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
  });
  if (prev) endQuiet(prev.at);
  return { groups, end: prev && { level: prev.level, at: prev.at, from, changes, tick: changes[changes.length - 1] || null } };
}

// Points per hour from the first of these changes of the level to the last (3 changes at least).
function rateOf(changes) {
  if (changes.length < STRETCH_TICKS) return 0;
  const first = changes[0];
  const last = changes[changes.length - 1];
  const points = Math.abs(first.level - last.level);
  const hours = (last.at - first.at) / HOUR;
  return points > 0 && hours > 0 ? points / hours : 0;
}

function stretchRate(end) {
  return end ? rateOf(end.changes) : 0;
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

// The glasses' level shown: the glasses' own reading first, else the phone's. `at`: when that level was last vouched
// for (now for the glasses' own reading, else the time of the phone's word).
function glassesShown(stale) {
  if (own) return { value: own.level, charging: own.charging, old: false, at: Date.now() };
  if (phone && phone.g >= 0) return { value: phone.g, charging: phone.gc === 'CHARGING', old: stale || phone.c === false, at: phone.at };
  return { value: -1, charging: !!phone && phone.gc === 'CHARGING', old: false, at: 0 };
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

// What makes a change of situation for "At this pace": worn or not, the glasses app in use, Follow my voice and Tidal
// playing. A field the installed phone build does not write (p) counts as off.
function situationKey(r) {
  return `${r.w}|${r.a || ''}|${!!r.m}|${!!r.p}`;
}

// What makes a change of situation besides worn or not: the app in use, Follow my voice, Tidal playing.
function useKey(r) {
  return `${r.a || ''}|${!!r.m}|${!!r.p}`;
}

// When the situation last changed inside the stretch that ends the readings (its first reading is history[from]): the
// time of the first reading after the change, or 0 when it held all through. When the phone's word now differs from the
// last reading in worn, the app in use or Follow my voice (they come only with an answer), it changed after the
// readings: Infinity. Tidal playing is taken from the readings only, because the phone's state messages carry Tidal's
// raw playing, which blinks off between songs.
// An off-the-face spell of under a minute between two worn readings is not a change: real readings have spells of a
// few seconds that look like the wear sensor rather than the glasses coming off, and each would start "At this pace"
// again (Director recheck 3, A3). Only what else changed across it counts. A spell still going at the end counts.
function switchedAt(from) {
  const last = history[history.length - 1];
  if (phone && last) {
    for (const k of ['w', 'a', 'm']) if (phone[k] !== undefined && last[k] !== undefined && phone[k] !== last[k]) return Infinity;
  }
  let i = history.length - 1;
  while (i > from) {
    const r = history[i];
    if (situationKey(r) === situationKey(history[i - 1])) {
      i--;
      continue;
    }
    if (r.w === 'DONNED' && history[i - 1].w === 'DOFFED') {
      let j = i - 1; // the first reading of the spell off the face that ends here
      while (j > from && history[j - 1].w === 'DOFFED') j--;
      if (j > from && r.at - history[j].at < BLIP) {
        const before = useKey(history[j - 1]);
        for (let k = j; k <= i; k++) if (useKey(history[k]) !== before) return history[k].at;
        i = j - 1;
        continue;
      }
    }
    return r.at;
  }
  return 0;
}

// The changes of the level behind "At this pace": those since the situation last changed that are also in the 30
// minutes before `asOf` (when the level shown was vouched for), 3 at least and spanning 5 minutes at least; else none.
// Three changes a minute apart are too few to set a pace: the phone sees whole percents, at uneven times (A3).
function paceChanges(end, asOf) {
  if (!end) return [];
  const from = Math.max(switchedAt(end.from), asOf - PACE);
  const used = end.changes.filter((c) => c.at >= from);
  return used.length >= STRETCH_TICKS && used[used.length - 1].at - used[0].at >= PACE_SPAN ? used : [];
}

// The line under the levels: when to charge, said as a clock time. Nothing is worked out from a level the phone vouched
// for over 30 minutes ago (a copy kept from an earlier visit, a phone that stopped answering), unless the glasses read
// their own battery; a younger one is worked out from its own time, not from now, and the top line dates it (Director
// recheck 1-3, C).
// - Charging: when they will reach 80%, from this charge's own readings, and nothing from 80% on. A charge runs at a
//   steady rate and slows near full, by an amount not measured on these glasses, so a straight line to 100% would be
//   early by an unknown margin (F).
// - Otherwise from the current stretch off the charger, from one change of the level to another; it starts after the
//   last reading that was charging, unknown or without the glasses, or where the readings break (a jump, a rise, 6 hours
//   with no reading), so a top-up or a reconnect starts it again.
//   - "At this pace: …" only from the changes since the situation last changed (a blip off the face aside) that are
//     also in the last 30 minutes, 3 at least and spanning 5 minutes at least, so it never blends two situations
//     (Director recheck 2, A2) and three quick changes do not set it (recheck 3, A3).
//   - Fewer: today's rate for what is going on now, when today's list has a row for it, said as such ("… if Tidal Glass
//     stays open").
//   - Else the pace over the whole stretch (3 changes at least), "At the pace since unplugging: …", or since the time it
//     starts when it did not start at the charger. It can span a change of situation, and its label says what it is.
function timeLine(G) {
  const none = { text: '' };
  if (!(G.value >= 0)) return none;
  const asOf = Math.min(G.at, Date.now());
  const old = Date.now() - G.at > LEVEL_FRESH;
  if (G.charging) {
    if (old || G.value >= CHARGED) return none;
    const { end } = measure(history, chargeLevel, () => 'charging', true);
    const rate = stretchRate(end);
    if (!rate) return none;
    // From the charge's last change, or from the level shown when it is ahead of it (the glasses' own reading).
    const [at, level] = G.value > end.tick.level ? [asOf, G.value] : [end.tick.at, end.tick.level];
    const charged = at + ((CHARGED - level) / rate) * HOUR;
    return charged > Date.now() ? { text: `${CHARGED}% around ${when(roundTime(charged))}`, charged } : none;
  }
  if (phone && phone.c === false) {
    const lost = lostAt();
    return { text: lost ? `Phone app lost the glasses at ${when(lost)}` : 'Phone app lost the glasses' };
  }
  if (old) return none;
  const { end } = measure(history, drainLevel, () => 'off');
  const changes = end ? end.changes : [];
  const rate = rateOf(changes);
  if (rate && G.value > end.tick.level + 1) return none; // higher than the phone's last reading: charged since
  const pace = rateOf(paceChanges(end, asOf));
  if (pace) return fromLastChange(G, end, pace, asOf, { lead: 'At this pace', both: true });
  const s = situation(current());
  const day = s && measure(withinDay(), drainLevel, situation).groups[s];
  if (day && day.points >= ROW_POINTS && day.hours >= ROW_HOURS) return targets(G.value, asOf, day.points / day.hours, { condition: IF_SAME[s] });
  return rate ? fromLastChange(G, end, rate, asOf, { lead: `At the pace ${since(end)}` }) : none;
}

// From the stretch's last change the level falls at this rate. A change overdue at `asOf` (when the level shown was
// vouched for) means it falls slower now, so it is taken as just about to change then; the level shown, when it is
// lower than the stretch's last change (the glasses' own reading, or the phone's word after the readings), starts then.
function fromLastChange(G, end, rate, asOf, how) {
  let at = Math.max(end.tick.at, asOf - HOUR / rate);
  let level = end.tick.level;
  if (G.value < level) {
    at = asOf;
    level = G.value;
  }
  return targets(level, at, rate, how);
}

// What the stretch's pace is measured since: unplugging, when the reading just before it was on the charger; otherwise
// (the phone lost the glasses, could not vouch for the level, or the readings broke) the time the stretch starts.
function since(end) {
  const before = history[end.from - 1];
  return before && before.c !== false && before.gc === 'CHARGING' ? 'since unplugging' : `since ${when(history[end.from].at)}`;
}

function withinDay() {
  const from = Date.now() - WINDOWS.day;
  return history.filter((r) => r.at >= from);
}

// When the level reaches 20% and empty at this rate. `lead` says which pace it is; `condition` what the time depends
// on. The line must stay on one line: only "At this pace" adds the empty time, and only when it needs no day name.
function targets(level, at, rate, { lead = '', condition = '', both = false } = {}) {
  const low = at + ((level - LOW) / rate) * HOUR;
  const empty = at + (level / rate) * HOUR;
  const say = (text) => `${lead ? `${lead}: ${text}` : text[0].toUpperCase() + text.slice(1)}${condition ? ` ${condition}` : ''}`;
  if (level <= LOW) return { text: say(`empty around ${when(roundTime(empty))}`), empty };
  const first = `${LOW}% around ${when(roundTime(low))}`;
  if (both && !needsDay(roundTime(empty))) return { text: say(`${first} · empty around ${when(roundTime(empty))}`), low, empty };
  return { text: say(first), low, empty };
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

// How long a rate was measured over, short enough for its column: minutes within 10 hours, then hours, then days.
function duration(hours) {
  const mins = Math.round(hours * 60);
  if (mins < 60) return `${mins} min`;
  if (mins < 600) {
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    return m ? `${h} h ${m} min` : `${h} h`;
  }
  return hours < 47.5 ? `${Math.round(hours)} h` : `${Math.round(hours / 24)} days`;
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

// A time needs its day said when it is neither today nor within 12 hours.
function needsDay(ms) {
  return new Date(ms).toDateString() !== new Date().toDateString() && Math.abs(ms - Date.now()) >= 12 * HOUR;
}

// A clock time, with the day when it needs it.
function when(ms) {
  if (!ms) return 'earlier';
  if (!needsDay(ms)) return clock(ms);
  return `${new Date(ms).toLocaleDateString([], { weekday: 'short' })} ${clock(ms)}`;
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

// No broker has carried the link for 12 s: no answer can come either, so it is said as a warning too (Photographer
// recheck 3, S1).
function linkDown() {
  return !!downAt && !linkUp() && Date.now() - downAt > ANSWER_WAIT;
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
  $('state').classList.toggle('warn', waiting || linkDown());
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
  const age = bandAge > BAND_FRESH; // only ever with a band level
  if (phone && band < 0) note = BAND_WHY[phone.bw] || (heard ? 'Not read yet' : '');
  else if (age) note = `Read ${ago(bandAge)}`;
  else if (band >= 0) note = bandRate(inWindow);
  setText($('bandNote'), note);
  // "Read 3 h ago" says why the number above it is dim, so it is not dimmed with it.
  $('bandNote').classList.toggle('age', age);
  // A "read … ago" moves with the clock; it alone is drawn again each minute while shown.
  clearTimeout(ageTimer);
  if (bandAge > BAND_FRESH && document.visibilityState === 'visible') ageTimer = setTimeout(() => { version++; render(); }, MIN);

  const t = timeLine(G);
  const left = $('left');
  setText(left, t.text);
  setAttr(left, 'data-low', t.low ? String(Math.round(t.low)) : null);
  setAttr(left, 'data-empty', t.empty ? String(Math.round(t.empty)) : null);
  setAttr(left, 'data-charged', t.charged ? String(Math.round(t.charged)) : null);
  // A time worked out from the phone's level goes when that level turns 30 minutes old, even with nothing else new.
  clearTimeout(levelTimer);
  const due = G.at + LEVEL_FRESH - Date.now();
  if (t.text && !own && due > 0 && document.visibilityState === 'visible') levelTimer = setTimeout(() => { version++; render(); }, due + 50);

  // Why the list is empty, said once: no link (the top line says it), the phone not heard from yet, or too little. A
  // row needs about 30 minutes of one use, added up over the span (ROW_HOURS), not 30 minutes off the charger (L).
  const list = heard ? rows(inWindow) : [];
  renderList(list, heard ? 'Not enough readings yet: each use needs about 30 minutes in all'
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
  // With no rows and nothing to say (no link: the top line says it), the heading goes too, so it heads nothing (G).
  setHidden($('usesHead'), !list.length && !empty);
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
// A reading alone (often the level now, after a break) is a 3 px dot: the 4 px tick drawn before read as one of the
// band's dashes (Photographer recheck 3). Returns the line and the dots, each a path.
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
  const line = [];
  const dots = [];
  for (const s of segs) {
    const pts = s.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`);
    if (pts.length === 1) dots.push(`M${pts[0]}L${pts[0]}`); // drawn by its round cap
    else line.push(`M${pts.join('L')}`);
  }
  return { line: line.join(''), dots: dots.join('') };
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
  const g = linePath(readings, (r) => (r.c === false ? -1 : r.g), gNow, t0, end, xOf, yOf);
  const b = linePath(readings, (r) => (r.b >= 0 && !(r.bt > 0 && r.at - r.bt > BAND_STALE) ? r.b : -1), bNow, t0, end, xOf, yOf);
  setAttr($('lineGlasses'), 'd', g.line);
  setAttr($('dotsGlasses'), 'd', g.dots);
  setAttr($('lineBand'), 'd', b.line);
  setAttr($('dotsBand'), 'd', b.dots);
  // The legend names a device only when something of it is drawn (Director recheck 1-3, G).
  setHidden($('keyGlasses'), !g.line && !g.dots);
  setHidden($('keyBand'), !b.line && !b.dots);
  // When less than the span is held (a short history, or an answer cut at the most a message carries), the left label
  // says when the readings start, with the day when it is not today.
  setText($('since'), readings.length && t0 > end - windowMs ? when(t0) : (span === 'day' ? '24 h ago' : '7 days ago'));
  setAttr($('chart'), 'aria-label', `Battery levels over the ${span === 'day' ? 'last 24 hours' : 'last 7 days'}`);
}

// ---- visits and timers --------------------------------------------------------------------------------
// No steady tick: one timeout for the moment the phone counts as not answering or the link as down, one for calm, and
// none while calm with nothing asked and the link up.

function schedule() {
  clearTimeout(tickTimer);
  tickTimer = 0;
  if (!key || document.visibilityState !== 'visible') return;
  const now = Date.now();
  const due = [];
  if (asked && now - asked <= ANSWER_WAIT) due.push(asked + ANSWER_WAIT + 20);
  if (downAt && !linkUp() && now - downAt <= ANSWER_WAIT) due.push(downAt + ANSWER_WAIT + 20);
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
    clearTimeout(levelTimer);
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
