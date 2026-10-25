'use strict';

// Battery on Meta Ray-Ban Display: the glasses' and the band's battery now, and what uses them, from the readings
// the Tidal Glass phone app keeps (BatteryWatch: every change of either level, charging, wearing, the glasses app in
// use and Quran listening, kept 7 days). Asked for over the phone's own sealed link: one `batHistory` on opening and
// every 5 minutes while on screen; the answer comes back on bkr/<topic>/bat. The phone's retained state carries the
// levels too (gb, gc, bb), so they show the moment a broker connects.
//
// Only what the phone can see is counted: wearing, charging, which of Bakar's glasses apps is in use, Quran listening.
// Meta's own apps, the display's brightness and the radios are not visible to it, so the page never names them.

const BROKERS = [
  { url: 'wss://broker.emqx.io:8084/mqtt' },
  { url: 'wss://broker.hivemq.com:8884/mqtt' },
  { url: 'wss://public.cloud.shiftr.io', username: 'public', password: 'public' },
];
const ASK_EVERY = 5 * 60000;
const ANSWER_WAIT = 12000;
const CALM_AFTER = 15000;
const LOW = 20;
const WINDOWS = { day: 24 * 3600000, week: 7 * 24 * 3600000 };
const $ = (id) => document.getElementById(id);

let topic = null;
let key = null;
let links = [];
let asked = 0; // time stamp of the request this visit is waiting for (0: answered or none)
let askTimer = 0;
let tickTimer = 0;
let hideTimer = 0;
let lastInputAt = Date.now();
let span = 'day';
let now = null; // the levels now: { g, gc, w, c, b, at }
let history = []; // readings, oldest first
let answeredAt = 0;
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
      client.subscribe([`bkr/${topic}/s`, `bkr/${topic}/bat`], { qos: 1 });
      // A broker that connects while a request is unanswered gets it too: the phone may be on that one only.
      if (asked) publishRequest(client);
      render();
    });
    client.on('message', (t, payload) => handle(new TextDecoder().decode(payload)));
    client.on('close', render);
    client.on('error', () => { /* mqtt.js reconnects by itself */ });
    links.push(client);
  }
}

function closeLink() {
  const old = links;
  links = [];
  for (const l of old) { try { l.end(true); } catch (e) { /* already closed */ } }
}

let requestBody = '';

async function ask() {
  asked = Date.now();
  // 24 hours by default (a few hundred readings); the week only when asked for, so a message stays small.
  requestBody = await seal({ c: 'batHistory', ts: asked, since: asked - WINDOWS[span], max: 1500 });
  for (const l of links.filter((x) => x.connected)) publishRequest(l);
  render();
}

function publishRequest(client) {
  if (requestBody) client.publish(`bkr/${topic}/c`, requestBody, { qos: 1 });
}

async function handle(text) {
  if (recent.includes(text)) return; // the same message from another broker
  recent.push(text);
  if (recent.length > 12) recent.shift();
  let m;
  try { m = await openMessage(text); } catch (e) { return; }
  if (m.t === 'state') {
    // The phone's retained state: the levels, until the full readings arrive.
    if (!answeredAt || (m.at || 0) > answeredAt) {
      now = {
        g: m.gb != null ? m.gb : (now ? now.g : -1),
        gc: m.gc ? 'CHARGING' : (now && now.gc === 'CHARGING' && m.gb == null ? now.gc : ''),
        w: now ? now.w : '?', c: m.gb != null, b: m.bb != null ? m.bb : (now ? now.b : -1), at: m.at || Date.now(),
      };
    }
    render();
  } else if (m.t === 'bat' && m.re === asked) {
    asked = 0;
    answeredAt = m.at || Date.now();
    now = settle({ ...m.now, at: answeredAt });
    history = Array.isArray(m.h) ? m.h.filter((r) => typeof r.at === 'number').map(settle) : [];
    render();
  }
}

// ---- what the readings say ---------------------------------------------------------------------------

// Meta's toolkit reports the glasses at 0% with charging unknown until the link settles: that is "not known yet",
// not an empty battery (it drew a drop to 0% on the real phone's chart, 2026-10-25 18:55).
function settle(r) {
  return r.g === 0 && r.gc !== 'CHARGING' && r.gc !== 'NOT_CHARGING' ? { ...r, g: -1 } : r;
}

const PLACES = {
  notworn: 'Not worn',
  listening: 'Worn, Quran listening',
  'app:tidal': 'Worn, Tidal Glass in use',
  'app:sound': 'Worn, Sound Lab in use',
  'app:quran': 'Worn, Quran Glass in use',
  worn: 'Worn, other apps or none',
};

function situation(r) {
  if (r.w === 'DOFFED') return 'notworn';
  if (r.m) return 'listening';
  return r.a ? `app:${r.a}` : 'worn';
}

// Percentage points per hour between consecutive readings of one device, while it is not charging; the situation is
// that of the earlier reading (what was going on while the level fell). A gap over 6 h is a phone that was off.
function drain(readings, field) {
  const out = {};
  let prev = null;
  for (const r of readings) {
    const level = r[field];
    if (level == null || level < 0) continue;
    const charging = field === 'g' ? (x) => x.gc === 'CHARGING' : () => false;
    if (prev && !charging(prev) && !charging(r) && level <= prev[field]) {
      const hours = (r.at - prev.at) / 3600000;
      if (hours > 0 && hours < 6) {
        const s = situation(prev);
        const o = out[s] || (out[s] = { points: 0, hours: 0 });
        o.points += prev[field] - level;
        o.hours += hours;
      }
    }
    prev = r;
  }
  return out;
}

// Time left on the glasses at the use of the last 90 minutes (needs 20 minutes and a fall of 2 points), else at the
// 24-hour rate for what is going on now; null when there is not enough to say.
function timeLeft(readings, level) {
  if (!now || level == null || level < 0 || now.gc === 'CHARGING') return null;
  const since = Date.now() - 90 * 60000;
  const lately = readings.filter((r) => r.at >= since && r.g >= 0 && r.gc !== 'CHARGING');
  if (lately.length >= 2) {
    const first = lately[0];
    const last = lately[lately.length - 1];
    const hours = (last.at - first.at) / 3600000;
    const fell = first.g - last.g;
    if (hours >= 1 / 3 && fell >= 2) return level / (fell / hours);
  }
  const day = drain(readings.filter((r) => r.at >= Date.now() - WINDOWS.day), 'g')[situation({ ...now, a: lastApp(readings), m: lastListening(readings) })];
  if (day && day.hours >= 1 / 3 && day.points >= 2) return level / (day.points / day.hours);
  return null;
}

function lastApp(readings) {
  return readings.length ? readings[readings.length - 1].a || '' : '';
}

function lastListening(readings) {
  return readings.length ? !!readings[readings.length - 1].m : false;
}

function duration(hours) {
  const mins = Math.round(hours * 60);
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

function clock(ms) {
  return new Date(ms).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

// ---- drawing ----------------------------------------------------------------------------------------

function render() {
  const paired = !!key;
  $('unpaired').hidden = paired;
  $('main').hidden = !paired;
  if (!paired) return;

  const g = now ? now.g : -1;
  const b = now ? now.b : -1;
  level($('glasses'), g, now && now.gc === 'CHARGING');
  level($('band'), b, false);
  $('glassesNote').textContent = !now ? '' : now.gc === 'CHARGING' ? 'Charging'
    : now.c === false ? 'Not connected to your phone'
    : now.w === 'DOFFED' ? 'Not worn' : now.w === 'DONNED' ? 'Worn' : '';

  const left = timeLeft(history, g);
  $('left').textContent = !now ? ''
    : now.gc === 'CHARGING' ? 'Charging now'
    : left != null ? `About ${duration(left)} left at your recent use`
    : 'Not enough use without charging yet to estimate the time left';

  const windowMs = WINDOWS[span];
  const inWindow = history.filter((r) => r.at >= Date.now() - windowMs);
  renderUses($('usesGlasses'), drain(inWindow, 'g'));
  // The band uses little and steadily: one plain rate under its level, over everything it went through.
  const band = Object.values(drain(inWindow, 'b')).reduce((s, o) => ({ points: s.points + o.points, hours: s.hours + o.hours }), { points: 0, hours: 0 });
  $('bandNote').textContent = b < 0 ? 'Not read yet'
    : band.hours >= 1 ? `Uses about ${(band.points / band.hours).toFixed(1)}% an hour`
    : '';
  renderChart(inWindow, windowMs);
  $('span').textContent = span === 'day' ? 'Last 24 hours' : 'Last 7 days';
  $('span').setAttribute('aria-label', span === 'day' ? 'Showing the last 24 hours. Press for 7 days' : 'Showing the last 7 days. Press for 24 hours');

  // The top line: where the numbers come from, and how fresh they are.
  const waiting = asked && Date.now() - asked > ANSWER_WAIT;
  $('stateText').textContent = !linkUp() ? (answeredAt ? `Reconnecting… · from ${clock(answeredAt)}` : 'Connecting to your phone…')
    : waiting ? (answeredAt ? `Your phone is not answering · from ${clock(answeredAt)}` : 'Your phone is not answering')
    : answeredAt ? `From your phone · ${clock(answeredAt)}` : 'Asking your phone…';
  $('state').classList.toggle('warn', !!waiting);
  $('main').classList.toggle('stale', !!waiting || (!linkUp() && !!answeredAt));
}

function level(el, value, charging) {
  const known = value != null && value >= 0;
  el.querySelector('.pct').textContent = known ? `${value}%` : '—';
  el.classList.toggle('low', known && value <= LOW && !charging);
  el.classList.toggle('charging', !!charging);
  const fill = el.querySelector('.fill');
  fill.style.inlineSize = known ? `${Math.max(2, value)}%` : '0';
}

function renderUses(list, rates) {
  const rows = Object.entries(rates)
    .map(([s, o]) => ({ s, rate: o.points / o.hours, hours: o.hours, points: o.points }))
    .filter((r) => r.hours >= 1 / 6)
    .sort((a, b) => b.rate - a.rate)
    .slice(0, 4);
  list.replaceChildren();
  if (!rows.length) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = 'Not enough readings yet: about an hour off the charger';
    list.append(li);
    return;
  }
  for (const r of rows) {
    const li = document.createElement('li');
    const name = document.createElement('span');
    name.className = 'what';
    name.textContent = PLACES[r.s] || r.s;
    const rate = document.createElement('span');
    rate.className = 'rate';
    rate.textContent = `${r.rate < 10 ? r.rate.toFixed(1) : Math.round(r.rate)}% an hour`;
    const over = document.createElement('span');
    over.className = 'over';
    over.textContent = `over ${duration(r.hours)}`;
    li.append(name, rate, over);
    list.append(li);
  }
}

function renderChart(readings, windowMs) {
  const svg = $('chart');
  const w = 540;
  const h = 96;
  const end = Date.now();
  // The axis starts at the first reading when there is less than the whole span, so a short history fills it.
  const first = readings.length ? readings[0].at : end - windowMs;
  const t0 = Math.max(end - windowMs, Math.min(first, end - 30 * 60000));
  const xOf = (at) => (((at - t0) / (end - t0)) * w).toFixed(1);
  const yOf = (v) => (h - (v / 100) * (h - 2) - 1).toFixed(1);
  const line = (field, current) => {
    const pts = readings.filter((r) => r[field] != null && r[field] >= 0 && r.at >= t0).map((r) => [r.at, r[field]]);
    if (current != null && current >= 0) pts.push([end, current]); // carried to now, at the level shown above
    if (pts.length === 1) pts.unshift([Math.max(t0, pts[0][0] - 60000), pts[0][1]]); // a single reading: a short tick
    return pts.map(([at, v]) => `${xOf(at)},${yOf(v)}`).join(' ');
  };
  $('lineGlasses').setAttribute('points', line('g', now ? now.g : null));
  $('lineBand').setAttribute('points', line('b', now ? now.b : null));
  $('since').textContent = readings.length && t0 > end - windowMs ? clock(t0) : (span === 'day' ? '24 h ago' : '7 days ago');
  svg.setAttribute('aria-label', `Battery levels over the ${span === 'day' ? 'last 24 hours' : 'last 7 days'}`);
}

// ---- visits -----------------------------------------------------------------------------------------

function startVisit() {
  if (!key) return;
  clearTimeout(hideTimer);
  openLink();
  ask();
  clearInterval(askTimer);
  askTimer = setInterval(ask, ASK_EVERY);
  clearInterval(tickTimer);
  tickTimer = setInterval(() => { render(); checkCalm(); }, 1000);
}

// Battery: off screen, the link closes and the timers stop.
function onVisibility() {
  if (document.visibilityState === 'visible') startVisit();
  else {
    clearInterval(askTimer);
    clearInterval(tickTimer);
    asked = 0;
    hideTimer = setTimeout(() => { if (document.visibilityState !== 'visible') closeLink(); }, 400);
  }
}

function wakeUp() {
  lastInputAt = Date.now();
  document.body.classList.remove('calm');
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
  $('span').addEventListener('click', () => { span = span === 'day' ? 'week' : 'day'; render(); if (key) ask(); });
  window.addEventListener('online', () => { if (document.visibilityState === 'visible') { closeLink(); openLink(); ask(); } });
  if ('serviceWorker' in navigator) { try { navigator.serviceWorker.register('sw.js').catch(() => {}); } catch (e) { /* optional */ } }
  render();
  if (key) {
    $('span').focus();
    if (document.visibilityState === 'visible') startVisit();
  }
}

main();
