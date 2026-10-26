"""Battery (glasses app) tests. The MQTT library and the glasses' own battery are replaced by fakes and the phone is
played by the test, so no message leaves the PC. Service workers are blocked in every browser context: a page that a
service worker controls would fetch the real MQTT library and reach the real brokers.

Serves nothing itself: start `python -m http.server 47641 --bind 127.0.0.1 --directory webapp` first.
  python webapp/tests/battery_test.py [base_url] [shots_dir]
Set BATTERY_TEST_VERBOSE=1 to print what each check saw, passes included.

1-8   The first build's checks. Where the Director's review (W:\\Code\\GlassesProgram\\reviews\\battery-glass-v1\\
      director\\NOTES.md) changed wording or structure on purpose, the check says so.
B, T  The Director's harness cases (director\\scripts\\director_harness.py, its Sim class copied below), as checks
      that fail on 78e18cc.
D     The rest of the Director's must-fix items 1-7 and should-fix items 9-11 for the page.
N, A  The Director's recheck 1 (reviews\\battery-glass-v1\\recheck-1\\director\\NOTES.md): N11, N11r and N13 from
      its harness (scripts\\recheck_harness.py, same readings), and checks for must-fix A (the time left follows
      the pace now, and says which pace) and B (no "under X% an hour" from a span that ended in a jump). N11, N11r,
      both N13 checks and the two "since …" checks fail on bccfc7c; the 6-hour gap, line-fit and never-wraps checks
      are guards that pass on both.
P     The Photographer's recheck 1 (recheck-1\\photographer\\NOTES.md), must-fix A-C: the warning keeps its strength
      in calm, each bar runs its whole column, and "Read 3 h ago" stays bright. These fail on bccfc7c; the second
      P-C check (the Photographer's S05 side effect) is a guard that passes on both.
R, H  The Director's recheck 2 (recheck-2\\director\\NOTES.md). R: must-fix A2, "At this pace" only from the level
      changes since the situation last changed, with the readings of its scripts\\recheck2_harness.py (c_r2 from
      90.5%, c_r2h, c_r1); R2e, R2f, R2h6, R2d and R1 fail on 3b127b9. H: should-fix H, the link's retries back off as
      Tidal Glass build 17's (its webapp\\tests\\link_test.py check 7) and stop while hidden; fails on 3b127b9. With A2,
      D2 and the first since-reconnect check changed on purpose, and D11 changed with the unlinked hint; each says why.
Text is drawn in greyscale (--disable-lcd-text), as the glasses draw it, so the captures carry no colour fringes.
"""
import json
import math
import os
import re
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:47641/").rstrip("/") + "/"
SHOTS = Path(sys.argv[2]) if len(sys.argv) > 2 else None
URL = f"{BASE}index.html?test=1#t=bgtest&k={KEY}"
MIN = 60_000
HOUR = 60 * MIN
AMBER = "rgb(255, 176, 138)"
DIM = "rgb(152, 163, 174)"
WHITE = "rgb(255, 255, 255)"
FAKE_MQTT = """
window.__clients = [];
window.mqtt = { connect(url, opts) {
  const h = {};
  const c = { url, opts, connected: false, sent: [],
    on(ev, fn) { (h[ev] = h[ev] || []).push(fn); return c; },
    subscribe() {}, publish(t, body) { c.sent.push(body); }, end() { c.connected = false; },
    reconnect() { c.tries = (c.tries || 0) + 1; },
    fire(ev, ...a) {
      if (ev === 'connect') c.connected = true;
      if (ev === 'close') c.connected = false;
      (h[ev] || []).slice().forEach((f) => f(...a));
    } };
  window.__clients.push(c);
  return c;
} };
"""
# The glasses' own battery. By default Chromium's answer when it has no battery data at all (full, charging, never
# empties), which the page must treat as no data; __setBattery(level, charging) gives it a real reading.
FAKE_BATTERY = """
(() => {
  const on = {};
  const b = { level: 1, charging: true, chargingTime: 0, dischargingTime: Infinity,
    addEventListener(ev, fn) { (on[ev] = on[ev] || []).push(fn); }, removeEventListener() {} };
  window.__setBattery = (level, charging) => {
    Object.assign(b, { level, charging, chargingTime: charging ? 3600 : Infinity, dischargingTime: charging ? Infinity : 7200 });
    for (const ev of ['levelchange', 'chargingchange']) (on[ev] || []).forEach((f) => f());
  };
  Object.defineProperty(Navigator.prototype, 'getBattery', { configurable: true, value() { return Promise.resolve(b); } });
})();
"""
# How strongly an element is drawn: its opacity times every ancestor's (as the reviewers measured it).
EFF = """
window.__eff = (el) => {
  if (typeof el === 'string') el = document.querySelector(el);
  let o = 1;
  for (let e = el; e && e.nodeType === 1; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity);
  return Math.round(o * 1000) / 1000;
};
"""
ONE_LINE = 30  # px: the time-left line is about 24 px tall on one line, about 48 px on two


def day_of_readings(now_ms):
    """A made-up day: worn with Tidal Glass, off, a Quran session, worn with nothing, a charge, Tidal again."""
    out, t = [], now_ms - 9 * 3600_000
    band = 100

    def run(start, end, minutes, w="DONNED", a="", m=False, charging=False):
        nonlocal t, band
        steps = abs(end - start)
        for i in range(steps + 1):
            level = start + (i if end > start else -i)
            out.append({"at": int(t), "g": level, "gc": "CHARGING" if charging else "NOT_CHARGING", "w": w, "c": True,
                        "b": band, "a": a, "m": m})
            t += minutes * 60_000 / max(1, steps)
            if not charging and i % 6 == 5:
                band = max(0, band - 1)

    run(100, 82, 60, a="tidal")          # 18 %/h
    run(82, 78, 120, w="DOFFED")        # 2 %/h
    run(78, 64, 40, m=True, a="quran")  # 21 %/h
    run(64, 50, 80)                     # 10.5 %/h
    run(50, 95, 60, charging=True)
    run(95, 80, 60, a="tidal")          # 15 %/h
    return out


class Sim:
    """Writes readings as BatteryWatch would, from a continuously falling true level.

    Copied unchanged from the Director's harness (W:\\Code\\GlassesProgram\\reviews\\battery-glass-v1\\director\\
    scripts\\director_harness.py), so this test stands on its own. Newer fields (p) go in through set()."""

    def __init__(self, start_ms, level, band=90.0, band_rate=1.0):
        self.t = start_ms
        self.level = float(level)
        self.band = float(band)
        self.band_rate = band_rate
        self.f = {"gc": "NOT_CHARGING", "w": "DONNED", "c": True, "a": "", "m": False}
        self.out = []
        self.shown_g = self.shown()
        self.shown_b = int(math.ceil(self.band - 1e-9))
        self.next_band_read = start_ms + 15 * 60_000
        self.emit()

    def shown(self):
        return int(math.ceil(self.level - 1e-9))

    def emit(self, **override):
        r = {"at": int(self.t), "g": self.shown_g, **self.f, "b": self.shown_b}
        r.update(override)
        # BatteryWatch writes a line only when something differs from the last one
        if self.out and {k: v for k, v in self.out[-1].items() if k != "at"} == {k: v for k, v in r.items() if k != "at"}:
            return
        self.out.append(r)

    def set(self, **fields):
        self.f.update(fields)
        self.emit()

    def run(self, minutes, rate, visible=True, step_s=5):
        """rate: true glasses drain in % per hour. visible=False: the phone cannot see the glasses (no readings)."""
        steps = int(minutes * 60 / step_s)
        for _ in range(steps):
            self.t += step_s * 1000
            self.level -= rate * step_s / 3600
            self.band -= self.band_rate * step_s / 3600
            changed = False
            if visible and self.shown() != self.shown_g:
                self.shown_g = self.shown()
                changed = True
            if self.t >= self.next_band_read:
                self.next_band_read += 15 * 60_000
                nb = int(math.ceil(self.band - 1e-9))
                if nb != self.shown_b:
                    self.shown_b = nb
                    changed = True
            if changed:
                self.emit()

    def now(self):
        last = self.out[-1]
        return {k: last[k] for k in ("g", "gc", "w", "c", "b")}


def charge(s, points, minutes_each):
    """A charge, one point at a time (as the harness's T2 case does inline)."""
    s.set(gc="CHARGING")
    for _ in range(points):
        s.t += minutes_each * 60_000
        s.level += 1
        s.shown_g = s.shown()
        s.emit()


def now_of(s, **extra):
    """The phone's word now, as the newer phone build sends it (with a, m, p and the band's read time)."""
    return {**s.now(), "a": s.f["a"], "m": s.f["m"], "p": s.f.get("p", False), "bt": s.t - 5 * MIN, **extra}


def normal_day(now_ms):
    """A day as BatteryWatch would write it: Tidal Glass, music, off the face, Follow my voice, worn, Sound Lab."""
    s = Sim(now_ms - 417 * MIN, 100.0, band=97.0, band_rate=0.7)
    s.set(a="tidal"); s.run(50, 16)
    s.set(a="", p=True); s.run(60, 13)
    s.set(w="DOFFED", p=False); s.run(150, 2)
    s.set(w="DONNED", m=True); s.run(40, 22)
    s.set(m=False); s.run(70, 9)
    s.set(a="sound"); s.run(45, 12)
    s.set(a="")
    return s


failures = []
layout = {}
wraps = []  # every time-left line seen on more than one line


VERBOSE = bool(os.environ.get("BATTERY_TEST_VERBOSE"))  # print what each check saw, passes included


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:600]})" if detail and (VERBOSE or not ok) else ""))
    if not ok:
        failures.append(name)


def guarded(name, fn):
    """Runs one case; an error in it (for example a function an older build lacks) is that case's failure."""
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - any error fails the case, the rest still run
        check(name, False, f"error: {e}")


def fetch(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return r.read().decode("utf-8")


VIEW = """() => {
  const t = (id) => (document.getElementById(id) ? document.getElementById(id).textContent : null);
  const nc = document.getElementById('notCounted');
  return {
    state: t('stateText'), warn: document.getElementById('state').classList.contains('warn'),
    glasses: document.querySelector('#glasses .pct').textContent, glassesCls: document.getElementById('glasses').className,
    glassesNote: t('glassesNote'),
    band: document.querySelector('#band .pct').textContent, bandCls: document.getElementById('band').className,
    bandNote: t('bandNote'),
    left: t('left'), leftData: { ...document.getElementById('left').dataset },
    leftH: Math.round(document.getElementById('left').getBoundingClientRect().height),
    rows: [...document.querySelectorAll('#usesGlasses li')].map((li) => (li.children.length ? [...li.children].map((c) => c.textContent).join(' | ') : li.textContent)),
    heading: document.querySelector('.uses h2').textContent,
    notCounted: !!nc && !nc.hidden,
    since: t('since'),
    gPath: document.getElementById('lineGlasses').getAttribute('d') || '',
    bPath: document.getElementById('lineBand').getAttribute('d') || '',
  };
}"""


def rate_of(rows, name):
    """('rate', n) for "n% an hour", ('under', x) for "under x% an hour", None when there is no such row."""
    for r in rows:
        parts = r.split(" | ")
        if parts[0] != name:
            continue
        m = re.search(r"(under )?(\d+(?:\.\d+)?)% an hour", r)
        if not m:
            return ("?", r)
        return ("under" if m.group(1) else "rate", float(m.group(2)))
    return None


def near(found, value, within):
    return bool(found) and found[0] == "rate" and abs(found[1] - value) <= within


def path_pts(d):
    return [(float(x), float(y)) for x, y in re.findall(r"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", d or "")]


def clock_minutes(text):
    return [int(m) for m in re.findall(r"\d{1,2}:(\d{2})", text or "")]


def true_targets(at_ms, level, rate):
    """When a level falling at a steady rate (points an hour) reaches 20% and empty."""
    return at_ms + (level - 20) / rate * HOUR, at_ms + level / rate * HOUR


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", args=["--enable-spatial-navigation", "--disable-lcd-text"])
    errors = []

    def new_context():
        ctx = browser.new_context(viewport={"width": 600, "height": 600}, service_workers="block", locale="en-US",
                                  timezone_id="America/Toronto")
        ctx.add_init_script(FAKE_BATTERY)
        ctx.add_init_script(EFF)
        ctx.route("**/vendor/mqtt.min.js", lambda r: r.fulfill(status=200, content_type="application/javascript", body=FAKE_MQTT))
        return ctx

    def page_at(url, ctx=None, connect=False):
        pg = (ctx or new_context()).new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(url)
        if "#t=" in url:
            pg.wait_for_function("window.__clients && window.__clients.length === 3")
            if connect:
                pg.evaluate("window.__clients.forEach((c) => c.fire('connect'))")
                pg.wait_for_timeout(300)
        return pg

    def shot(pg, name):
        if SHOTS:
            SHOTS.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(SHOTS / f"{name}.png"))

    def answer(pg, now_levels, history, wait=250):
        pg.evaluate("""async ([nowLevels, h]) => {
            const body = await seal({ t: 'bat', re: asked, at: Date.now(), now: nowLevels, h });
            await handle(body);
        }""", [now_levels, history])
        pg.wait_for_timeout(wait)

    def view(pg):
        v = pg.evaluate(VIEW)
        if v["leftH"] > ONE_LINE:
            wraps.append([v["left"], v["leftH"]])
        return v

    def clock_of(pg, ms):
        return pg.evaluate("(at) => new Date(at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })", ms)

    def close(pg):
        pg.context.close()

    # ---- 1. Unpaired --------------------------------------------------------------------------------------
    pg = page_at(f"{BASE}index.html?test=1")
    pg.wait_for_timeout(400)
    check("1 without a pairing the page says how it gets linked",
          pg.evaluate("!document.getElementById('unpaired').hidden && document.getElementById('main').hidden"))
    hint = pg.evaluate("document.querySelector('#unpaired .hint').textContent")
    # Changed on purpose (coordinator, 2026-10-25): the installed phone build (18:59) has no "Put Battery on my glasses"
    # button; on it Battery is added from the Tidal Glass app's "Add Battery to your glasses" notification, which the
    # coordinator sends. So the hint names only what both builds have, as live 20bc2c3 did. The button's name can come
    # back after the next phone install (README).
    check("D11 the unlinked screen's hint is true on both phone builds: added from the Tidal Glass app, no button only "
          "the next build has", hint == "It is added from the Tidal Glass app on your phone, which links it.", hint)
    shot(pg, "1-unpaired")
    close(pg)

    # ---- 2-8. Paired ----------------------------------------------------------------------------------------
    pg = page_at(URL)
    hosts = pg.evaluate("window.__clients.map((c) => new URL(c.url).hostname)")
    check("2 all three brokers open together", sorted(hosts) == ["broker.emqx.io", "broker.hivemq.com", "public.cloud.shiftr.io"], hosts)
    shiftr = hosts.index("public.cloud.shiftr.io")
    hive = hosts.index("broker.hivemq.com")
    pg.evaluate("(i) => window.__clients[i].fire('connect')", shiftr)
    pg.wait_for_timeout(300)
    reqs = pg.evaluate("async (i) => Promise.all(window.__clients[i].sent.map((b) => openMessage(b)))", shiftr)
    ok2 = len(reqs) == 1 and reqs[0]["c"] == "batHistory" and abs((reqs[0]["ts"] - reqs[0]["since"]) - 24 * 3600_000) < 5
    check("2 the first connect sends one sealed batHistory for the last 24 hours", ok2, json.dumps(reqs)[:200])
    pg.evaluate("(i) => window.__clients[i].fire('connect')", hive)
    pg.wait_for_timeout(200)
    same = pg.evaluate("([a, b]) => window.__clients[b].sent.includes(window.__clients[a].sent[0])", [shiftr, hive])
    check("3 a broker connecting while the request is unanswered gets the same request", same)

    now_ms = pg.evaluate("Date.now()")
    hist = day_of_readings(now_ms)
    last = hist[-1]
    hist.append({"at": now_ms - 60000, "g": 0, "gc": "", "w": "DONNED", "c": True, "b": last["b"], "a": "", "m": False})  # toolkit not settled
    answer(pg, {"g": last["g"], "gc": "NOT_CHARGING", "w": "DONNED", "c": True, "b": last["b"]}, hist)
    v = view(pg)
    g_pts = path_pts(v["gPath"])
    b_pts = path_pts(v["bPath"])
    # Wording changed on purpose (Director items 2, 5, 11): the time left is a clock time ("... around ..."), the top
    # row is "Follow my voice (glasses mic on)" (was "Worn, Quran listening"), and the lines are paths that can break
    # (were polylines), so their points are read from `d`.
    ok4 = (v["glasses"] == f"{last['g']}%" and v["band"] == f"{last['b']}%" and "around" in v["left"]
           and v["rows"] and v["rows"][0].startswith("Follow my voice (glasses mic on)") and len(g_pts) > 50 and len(b_pts) > 50
           and v["state"].startswith("From your phone") and max(y for _, y in g_pts) < 90)
    check("4 the answer draws both levels, the time left, how fast they drain (highest first) and both lines, never a 0% that was only unknown", ok4, json.dumps(v)[:500])

    # 5. low and charging
    answer(pg, {"g": 14, "gc": "NOT_CHARGING", "w": "DONNED", "c": True, "b": 9}, hist)
    low = pg.evaluate("[document.getElementById('glasses').classList.contains('low'), document.getElementById('band').classList.contains('low'), getComputedStyle(document.querySelector('#glasses .pct')).color, getComputedStyle(document.getElementById('glassesNote')).color]")
    answer(pg, {"g": 44, "gc": "CHARGING", "w": "DOFFED", "c": True, "b": 60}, hist)
    chg = pg.evaluate("[document.getElementById('glasses').classList.contains('charging'), document.getElementById('glassesNote').textContent, document.getElementById('left').textContent]")
    # Wording changed on purpose (Director items 2, 10): the line under the levels no longer repeats "Charging now";
    # it says when they will be full, or nothing.
    check("5 a low level is amber; charging is green and says so once", low[0] and low[1] and low[2] == AMBER and chg[0]
          and chg[1] == "Charging" and "Charging" not in chg[2], json.dumps([low, chg]))
    check("D10 a low level colours only its number and bar, not the note under it", low[3] != AMBER, low)

    # 6. not answering
    pg.evaluate("ask()")
    pg.wait_for_timeout(100)
    pg.evaluate("asked = Date.now() - 13000; render()")
    warn = pg.evaluate("[document.getElementById('stateText').textContent, document.getElementById('state').classList.contains('warn')]")
    warn.append(pg.evaluate("[document.getElementById('main').classList.contains('stale'), __eff('#glasses .pct'), __eff('#band .pct')]"))
    # Changed on purpose (Director item 6): each level from the phone dims on its own, so a level the glasses read
    # themselves can stay bright; the check reads the two levels, not their shared row. Changed again on purpose
    # (Photographer recheck 1, C): an old level dims its parts, not its box, so its age note can stay bright; the check
    # reads how strongly each number is drawn.
    check("6 no answer for 12 s: the phone is not answering, in the warning look, and the last word dims",
          warn[0].startswith("Your phone is not answering") and warn[1] and warn[2][0] and warn[2][1] < 1 and warn[2][2] < 1, warn)

    # 7. span
    pg.evaluate("asked = 0; window.__clients.forEach((c) => { c.sent.length = 0; })")
    pg.focus("#span")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(300)
    reqs = pg.evaluate("async (i) => Promise.all(window.__clients[i].sent.map((b) => openMessage(b)))", shiftr)
    ok7 = reqs and abs((reqs[-1]["ts"] - reqs[-1]["since"]) - 7 * 24 * 3600_000) < 5 and pg.evaluate("document.getElementById('span').textContent") == "Last 7 days"
    check("7 the span button asks again for 7 days", bool(ok7), json.dumps(reqs)[:200])

    # 8. calm
    pg.evaluate("lastInputAt = Date.now() - 16000; checkCalm()")
    calm = pg.evaluate("""() => [document.body.classList.contains('calm'),
        getComputedStyle(document.querySelector('.uses')).opacity, getComputedStyle(document.querySelector('#glasses .pct')).opacity]""")
    check("8 calm dims the details, never the levels", calm[0] and float(calm[1]) < 1 and float(calm[2]) == 1, calm)
    close(pg)

    # ---- B01-B05, T1-T3: the Director's harness cases -------------------------------------------------------

    def b01_day(now_ms):
        s = Sim(now_ms - (60 + 30 + 20 + 120 + 45) * 60_000 - 10_000, 100.0, band=92.0, band_rate=1.0)
        s.set(a="tidal")
        s.run(60, 18)                     # worn, Tidal Glass open, 18 %/h
        s.set(a="")
        s.run(30, 10)                     # worn, nothing of ours, 10 %/h
        s.set(c=False)                    # the phone loses the glasses; worn and level stay as last known
        s.run(20, 2, visible=False)       # really off the face, 2 %/h, unseen
        s.t += 2000
        blip_at = s.t
        s.f.update(c=True, w="DOFFED", gc="UNKNOWN")
        s.emit(g=0)                       # the toolkit's 0 with charging unknown
        s.t += 5000
        s.f.update(gc="NOT_CHARGING")
        s.shown_g = s.shown()
        s.emit()
        s.run(120, 2)                     # not worn, 2 %/h
        s.set(w="DONNED", a="tidal")
        s.run(45, 18)                     # Tidal Glass again
        return s, blip_at

    def case_b01():
        pg = page_at(URL, connect=True)
        s, _ = b01_day(pg.evaluate("Date.now()"))
        answer(pg, s.now(), s.out)
        v = view(pg)
        rates = [float(x) for x in re.findall(r"(?<!under )\b(\d+(?:\.\d+)?)% an hour", " ".join(v["rows"]))]
        pts = path_pts(v["gPath"])
        ok = (rates and max(rates) <= 25 and near(rate_of(v["rows"], "Tidal Glass open"), 18, 2)
              and (rate_of(v["rows"], "Not worn") is None or near(rate_of(v["rows"], "Not worn"), 2, 1))
              and len(pts) > 10 and max(y for _, y in pts) < 60)
        check("B01 a reconnect's 0% never becomes a drain row or a drop on the chart (true: Tidal Glass 18, not worn 2 an hour)", ok, v)
        close(pg)

    def case_b02():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s, blip_at = b01_day(now_ms)
        h = [r for r in s.out if r["at"] <= blip_at]
        now_b = {k: h[-1][k] for k in ("g", "gc", "w", "c", "b")}
        shift = now_ms - blip_at
        h = [{**r, "at": r["at"] + shift} for r in h]
        answer(pg, now_b, h)
        v = view(pg)
        ok = (v["glasses"] == "—" and "low" not in v["glassesCls"].split() and v["glassesNote"] == "" and v["left"] == "")
        check("B02 asked during the toolkit's 0: the glasses show unknown, with no amber, no note and no time left", ok, v)
        close(pg)

    def case_b03():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = Sim(now_ms - (56.9 + 12 + 40) * 60_000 - 10_000, 80.0, band=88.0, band_rate=0.0)
        s.set(a="tidal")
        s.run(56.9, 20)                   # Tidal Glass, 20 %/h: a tick every 3 min
        s.set(w="DOFFED", a="")           # off 2.9 min after the last tick
        s.run(12, 2)                      # not worn, true 2 %/h
        s.set(w="DONNED")
        s.run(40, 10)                     # worn, nothing of ours, 10 %/h
        answer(pg, s.now(), s.out)
        v = view(pg)
        nw = rate_of(v["rows"], "Not worn")
        worn = rate_of(v["rows"], "Worn, anything else")
        ok = (near(rate_of(v["rows"], "Tidal Glass open"), 20, 2) and (nw is None or (nw[0] == "rate" and nw[1] <= 3))
              and (worn is None or near(worn, 10, 2)))
        check("B03 a 1% change during Tidal Glass is not credited to the minutes after taking them off (true: not worn 2 an hour)", ok, v)
        close(pg)

    def case_b04():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = Sim(now_ms - 68.5 * 60_000 - 10_000, 70.0, band=88.0, band_rate=0.5)
        s.run(48.5, 10)                   # worn, 10 %/h, 8 ticks, the last 0.5 min before taking them off
        s.set(w="DOFFED")
        s.run(20, 2)                      # not worn 20 min at 2 %/h: 0.67 points, no tick
        s.t += 5000
        s.set(w="DONNED")                 # put on again: the reading that closes the not-worn stretch
        answer(pg, s.now(), s.out)
        v = view(pg)
        text = " ".join(v["rows"] + [v["bandNote"], v["left"]])
        ok = (not re.search(r"(?<![\d.])0(\.0)?% an hour", text) and rate_of(v["rows"], "Not worn") is None
              and near(rate_of(v["rows"], "Worn, anything else"), 10, 2)
              and (v["bandNote"] == "" or "under" in v["bandNote"]) and ".0%" not in text)
        check("B04 no fall and no change never read as 0% an hour (band: under 1%, not worn: no row from 20 minutes)", ok, v)
        close(pg)

    def case_b05():
        pg = page_at(URL, connect=True)
        at = pg.evaluate("Date.now() - 3 * 3600000")
        pg.evaluate("""async (at) => {
            const body = await seal({ t: 'state', at, play: false, gb: 64, bb: 71 });
            await handle(body);
        }""", at)
        pg.wait_for_timeout(200)
        pg.evaluate("asked = Date.now() - 13000; render()")
        v = view(pg)
        when = pg.evaluate("(at) => new Date(at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })", at)
        ok = (v["state"].startswith("Your phone is not answering") and f"levels from {when}" in v["state"]
              and v["glasses"] == "64%" and "old" in v["glassesCls"].split() and v["band"] == "71%" and "old" in v["bandCls"].split()
              and v["rows"] == ["Waiting for your phone's readings"] and v["left"] == "")
        check("B05 a 3-hour-old copy of the levels says when it is from, dims, and the list waits for the phone", ok, v)
        shot(pg, "B05-old-copy")
        close(pg)

    def case_t1():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = Sim(now_ms - 100 * 60_000, 80.0)
        s.run(100, 12)
        n = s.now()
        n["g"] = -1                       # the phone has not heard the glasses' level
        answer(pg, n, s.out)
        v = view(pg)
        dash = pg.evaluate("getComputedStyle(document.querySelector('#glasses .pct')).color")
        ok = v["glasses"] == "—" and v["left"] == "" and near(rate_of(v["rows"], "Worn, anything else"), 12, 2)
        check("T1 the glasses' level unknown: no time-left line, while the list still stands", ok, v)
        check("D10 an unknown level's dash is dimmed", dash == DIM, dash)
        close(pg)

    def case_t2():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = Sim(now_ms - 90 * 60_000, 60.0, band_rate=0.0)
        s.run(20, 12)                     # 60 -> 56
        s.set(gc="CHARGING")
        for _ in range(10):               # charge +10 over 10 min
            s.t += 60_000
            s.level += 1
            s.shown_g = s.shown()
            s.emit()
        s.set(gc="NOT_CHARGING")
        s.run(60, 12)                     # 66 -> 54, true 12 %/h
        answer(pg, s.now(), s.out)
        v = view(pg)
        expected = now_ms + s.now()["g"] / 12 * HOUR
        empty = int(v["leftData"].get("empty", "0"))
        mins = clock_minutes(v["left"])
        ok = ("empty around" in v["left"] and abs(empty - expected) < 20 * MIN and " if " not in v["left"]
              and len(mins) == 2 and mins[1] % 30 == 0 and mins[0] % 15 == 0)
        check("T2 after a short top-up the time left comes from this stretch off the charger (true: empty in 4 h 30 min), "
              "said as clock times rounded to 15 and 30 minutes", ok, [v["left"], v["leftData"], (empty - now_ms) / MIN])
        close(pg)

    def case_t3():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        pg.evaluate("asked = 0; span = 'week'; render(); ask()")
        pg.wait_for_timeout(200)
        big = [{"at": now_ms - (1500 - i) * 4 * 60_000, "g": 90 - (i % 60), "gc": "NOT_CHARGING", "w": "DONNED",
                "c": True, "b": 70, "a": "", "m": False} for i in range(1500)]
        answer(pg, {"g": 31, "gc": "NOT_CHARGING", "w": "DONNED", "c": True, "b": 70}, big)
        v = view(pg)
        pts = path_pts(v["gPath"])
        ok = (v["since"] != "7 days ago" and re.match(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d", v["since"] or "")
              and pts and pts[0][0] < 5)
        check("T3 a 7-day answer cut at 1,500 readings is labelled from its oldest reading, with the day, and fills the chart",
              bool(ok), [v["since"], pts[:1]])
        # No older readings can be had, so the next request asks only for newer ones (not the whole week again).
        pg.evaluate("window.__clients.forEach((c) => { c.sent.length = 0; }); ask()")
        pg.wait_for_timeout(200)
        req = pg.evaluate("async () => openMessage(window.__clients[0].sent[window.__clients[0].sent.length - 1])")
        check("D9 after a 7-day answer cut at 1,500 readings, the next request asks only for newer readings",
              req.get("since") == big[-1]["at"], req)
        close(pg)

    for name, fn in [("B01", case_b01), ("B02", case_b02), ("B03", case_b03), ("B04", case_b04), ("B05", case_b05),
                     ("T1", case_t1), ("T2", case_t2), ("T3", case_t3)]:
        guarded(name, fn)

    # ---- D: the other items ---------------------------------------------------------------------------------

    def case_no_link():
        pg = page_at(URL, connect=False)
        pg.wait_for_timeout(500)
        v = view(pg)
        ok = v["state"].startswith("Connecting to your phone") and v["rows"] == [] and v["bandNote"] == "" and v["left"] == ""
        check("D7 no link: the list stays empty and no note gives another reason; the top line says it", ok, v)
        close(pg)

    def case_not_enough():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = Sim(now_ms - 12 * MIN, 70.0)
        s.run(12, 10)
        answer(pg, s.now(), s.out)
        v = view(pg)
        ok = v["rows"] == ["Not enough readings yet: about 30 minutes off the charger"] and not v["notCounted"]
        check("D7 answered with too few readings: \"Not enough readings yet: about 30 minutes off the charger\"", ok, v)
        close(pg)

    def case_normal_day():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = normal_day(now_ms)
        answer(pg, now_of(s), s.out)
        v = view(pg)
        rows = v["rows"]
        names = [r.split(" | ")[0] for r in rows]
        expect = {"Follow my voice (glasses mic on)": 22, "Tidal Glass open": 16, "Music playing": 13,
                  "Sound Lab open": 12, "Worn, anything else": 9, "Not worn": 2}
        ok_rows = all(near(rate_of(rows, k), val, 2 if val > 5 else 1) for k, val in expect.items())
        check("D5 six situations, named for what the phone sees, in whole numbers, highest first",
              ok_rows and names[0] == "Follow my voice (glasses mic on)" and not any("." in r.split(" | ")[-1] for r in rows), rows)
        check("D5 the heading is \"How fast they drain\" with one dim line on what is not counted",
              v["heading"] == "How fast they drain" and v["notCounted"]
              and pg.evaluate("document.getElementById('notCounted').textContent") == "Not counted: Meta's apps, brightness, and Quran Glass outside Follow my voice.",
              [v["heading"], v["notCounted"]])
        fit = pg.evaluate("""() => {
            const uses = document.querySelector('.uses').getBoundingClientRect();
            const parts = [...document.querySelectorAll('#usesGlasses li'), document.getElementById('notCounted')];
            const chart = document.getElementById('chart').getBoundingClientRect();
            return { inside: parts.every((el) => !!el && el.getBoundingClientRect().bottom <= uses.bottom + 0.5),
                     above: uses.bottom <= chart.top + 0.5,
                     clipped: [...document.querySelectorAll('#usesGlasses .what')].some((el) => el.scrollWidth > el.clientWidth + 1) };
        }""")
        check("D5 all six rows and the \"Not counted\" line fit above the chart, no name cut", fit["inside"] and fit["above"] and not fit["clipped"], fit)
        mins = clock_minutes(v["left"])
        # Changed on purpose (Director recheck 1, A): the line says which pace it uses. Changed again on purpose
        # (Director recheck 2, A2): this day closes Sound Lab 2 minutes before the answer, so no level change has come
        # since the situation changed, and "At this pace" would give Sound Lab's pace under a label that says it is
        # the pace now (3b127b9 did). The line falls back to today's rate for what is going on now (worn, nothing
        # open), said as such. The two-time "At this pace" line is checked in N11, R2e, R2f and R2h6.
        check("D2 the time left is a clock time, rounded: Sound Lab closed 2 minutes ago, so not \"At this pace\" but "
              "today's rate for being worn, \"... if you keep them on\"",
              re.fullmatch(r"20% around \d{1,2}:\d{2}\W+[AP]M if you keep them on", v["left"] or "") is not None
              and len(mins) == 1 and all(m % 5 == 0 for m in mins) and v["glassesNote"] == "", [v["left"], v["glassesNote"]])
        check("D4 the band's note is a whole number", re.fullmatch(r"Uses (about \d+|under \d+)% an hour", v["bandNote"] or "") is not None, v["bandNote"])
        layout["left_top"] = pg.evaluate("document.getElementById('left').getBoundingClientRect().top")
        shot(pg, "2-day")

        # Should-fix 9: a still screen makes no DOM changes (78e18cc redrew everything every second).
        n = pg.evaluate("""async () => {
            document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Shift' }));
            let n = 0;
            const mo = new MutationObserver((list) => { n += list.length; });
            mo.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
            await new Promise((r) => setTimeout(r, 4000));
            mo.disconnect();
            return n;
        }""")
        check("D9 a still screen changes nothing in 4 seconds", n <= 1, n)

        # Should-fix 9: the next request asks only for what is newer than the newest reading held, and merges it.
        # The Sim writes two readings at one instant when a change follows a tick, so the counts compare like with like.
        held = pg.evaluate("[history.length, new Set(history.map((r) => r.at)).size]")
        newest = s.out[-1]["at"]
        pg.evaluate("window.__clients.forEach((c) => { c.sent.length = 0; }); ask()")
        pg.wait_for_timeout(200)
        req = pg.evaluate("async () => openMessage(window.__clients[0].sent[window.__clients[0].sent.length - 1])")
        more_at = pg.evaluate("Date.now()")
        more = [dict(s.out[-1])] + [{**s.out[-1], "at": more_at - (3 - i) * 30_000, "g": s.out[-1]["g"] - 1 - i} for i in range(2)]
        answer(pg, {**now_of(s), "g": more[-1]["g"]}, more)
        merged = pg.evaluate("[history.length, new Set(history.map((r) => r.at)).size, document.querySelector('#glasses .pct').textContent]")
        check("D9 the next request asks since the newest reading held, and the answer is merged without doubles",
              req.get("since") == newest and merged[0] == held[0] + 2 and merged[1] == held[1] + 2 and merged[2] == f"{more[-1]['g']}%",
              [req, held, merged])

        # Should-fix 10: the focus ring is thin and white, with no glow; green stays for charging.
        ring = pg.evaluate("""() => { const b = document.getElementById('span'); b.focus();
            const cs = getComputedStyle(b); return [document.activeElement === b, cs.boxShadow, cs.borderTopColor, cs.borderTopWidth]; }""")
        check("D10 the focus ring is a thin white line with no glow", ring[0] and ring[1] == "none" and ring[2] == WHITE and float(ring[3].rstrip("px")) <= 2, ring)

        # Must-fix 3 (retained flag): a broker's stored copy never replaces what the phone said on this visit; a state
        # the phone publishes while the page listens does.
        g_before = pg.evaluate("document.querySelector('#glasses .pct').textContent")
        pg.evaluate("""async () => {
            const body = await seal({ t: 'state', at: Date.now(), play: false, gb: 30, bb: 50 });
            window.__clients[0].fire('message', 'bkr/bgtest/s', new TextEncoder().encode(body), { retain: true });
        }""")
        pg.wait_for_timeout(200)
        g_retained = pg.evaluate("document.querySelector('#glasses .pct').textContent")
        pg.evaluate("""async () => {
            const body = await seal({ t: 'state', at: Date.now() + 1000, play: true, gb: 29, bb: 90 });
            window.__clients[1].fire('message', 'bkr/bgtest/s', new TextEncoder().encode(body), { retain: false });
        }""")
        pg.wait_for_timeout(200)
        g_live = pg.evaluate("document.querySelector('#glasses .pct').textContent")
        check("D3 a broker's stored copy (retained) never replaces this visit's answer; a live state does",
              g_retained == g_before and g_live == "29%", [g_before, g_retained, g_live])
        close(pg)

    def case_saved_and_own():
        ctx = new_context()
        pg = page_at(URL, ctx=ctx, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = normal_day(now_ms)
        answer(pg, now_of(s), s.out)
        answered = pg.evaluate("document.getElementById('stateText').textContent")
        pg.close()
        # The next launch, with no link yet: the copy kept on the glasses shows at once, with its time.
        pg = page_at(URL, ctx=ctx, connect=False)
        pg.wait_for_timeout(300)
        v = view(pg)
        when = answered.split(" · ")[-1]
        ok = (v["state"] == f"Connecting to your phone… · levels from {when}" and v["glasses"] == f"{s.now()['g']}%"
              and "old" in v["glassesCls"].split() and v["band"] == f"{s.now()['b']}%" and len(v["rows"]) >= 5
              and len(path_pts(v["gPath"])) > 20)
        check("D6 the last answer, kept on the glasses, is drawn at once while the link connects, marked with its time", ok, v)
        # The glasses' own battery comes first, stays bright, and the top line then dates only the band.
        pg.evaluate("__setBattery(0.34, false)")
        pg.wait_for_timeout(200)
        v = view(pg)
        ok = (v["glasses"] == "34%" and "old" not in v["glassesCls"].split() and "old" in v["bandCls"].split()
              and v["state"] == f"Connecting to your phone… · band from {when}")
        check("D6 the glasses' own battery reading comes first and stays bright; the phone's band level says its time", ok, v)
        shot(pg, "6-no-link-saved")
        pg.evaluate("__setBattery(0.34, true)")
        pg.wait_for_timeout(200)
        check("D6 the glasses' own charging shows too", pg.evaluate("document.getElementById('glassesNote').textContent") == "Charging")
        close(pg)

    def case_charging():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = normal_day(now_ms - 40 * MIN)
        s.set(w="DOFFED")
        charge(s, 33, 1.2)                # 50 %/h for 40 minutes
        answer(pg, now_of(s), s.out)
        v = view(pg)
        full = int(v["leftData"].get("full", "0"))
        expected = s.t + (100 - s.now()["g"]) / 50 * HOUR
        times = pg.evaluate("document.getElementById('main').innerText.split('Charging').length - 1")
        ok = (v["glassesNote"] == "Charging" and v["left"].startswith("Full around ") and abs(full - expected) < 8 * MIN
              and times == 1 and all(m % 5 == 0 for m in clock_minutes(v["left"])))
        check("D10 charging: \"Charging\" once, and the line under it says when they will be full", ok, [v["glassesNote"], v["left"], times, (full - expected) / MIN])
        shot(pg, "4-charging")
        close(pg)

    def case_low():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = Sim(now_ms - 165 * MIN - 10_000, 50.0, band=40.0, band_rate=0.9)
        s.set(a="tidal"); s.run(60, 16)
        s.set(a=""); s.run(70, 10)
        s.set(p=True); s.run(35, 14)
        answer(pg, now_of(s), s.out)
        v = view(pg)
        # Changed on purpose (Director recheck 1, A): "At this pace: empty around …" (was "Empty around …").
        ok = (v["glasses"] == f"{s.now()['g']}%" and "low" in v["glassesCls"].split()
              and v["left"].startswith("At this pace: empty around ") and " · " not in v["left"])
        check("D2 at 20% or less the line says when they will be empty", ok, v)
        shot(pg, "3-low")
        close(pg)

    def case_band():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = normal_day(now_ms)
        out = [{**r, "b": -1} for r in s.out]
        answer(pg, {**now_of(s), "b": -1, "bw": "no-battery-service"}, out)
        v = view(pg)
        top = pg.evaluate("document.getElementById('left').getBoundingClientRect().top")
        lay = "left_top" in layout and abs(top - layout["left_top"]) < 0.5
        ok = v["band"] == "—" and v["bandNote"] == "Doesn't share its battery level" and lay
        check("D7 a band the phone cannot read says why, and nothing below it moves", ok, [v["band"], v["bandNote"], top, layout.get("left_top")])
        shot(pg, "7-band-unknown")
        close(pg)

        pg = page_at(URL, connect=True)
        s = normal_day(pg.evaluate("Date.now()"))
        answer(pg, now_of(s, bt=s.t - 3 * HOUR), s.out)
        v = view(pg)
        check("D3 a band level read 3 hours ago says so and dims", v["bandNote"] == "Read 3 h ago" and "old" in v["bandCls"].split(), [v["bandNote"], v["bandCls"]])
        close(pg)

    def case_lost():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = Sim(now_ms - 100 * MIN, 80.0)
        s.set(a="tidal")
        s.run(60, 15)
        lost_at = s.t
        s.set(c=False)
        s.run(40, 10, visible=False)
        answer(pg, {**s.now(), "c": False}, s.out)
        v = view(pg)
        when = pg.evaluate("(at) => new Date(at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })", lost_at)
        check("D3 the phone app losing the glasses is said with its time", v["left"] == f"Phone app lost the glasses at {when}", [v["left"], when])
        close(pg)

    def case_gap():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        h = []
        for i in range(6):                # an evening: 60 -> 55, a point every 6 min
            h.append({"at": now_ms - 11 * HOUR + i * 6 * MIN, "g": 60 - i, "gc": "NOT_CHARGING", "w": "DONNED", "c": True, "b": 80, "a": "", "m": False})
        for i in range(5):                # the next morning, after an unseen night on the charger: 100 -> 96
            h.append({"at": now_ms - 3 * HOUR + i * 45 * MIN, "g": 100 - i, "gc": "NOT_CHARGING", "w": "DOFFED", "c": True, "b": 78, "a": "", "m": False})
        answer(pg, {"g": 96, "gc": "NOT_CHARGING", "w": "DOFFED", "c": True, "b": 78}, h)
        v = view(pg)
        check("D11 the chart breaks across a night the phone did not see, and keeps a quiet stretch joined",
              v["gPath"].count("M") == 2 and v["bPath"].count("M") == 1, [v["gPath"][:120], v["bPath"][:80]])
        close(pg)

    def case_columns():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = Sim(now_ms - (594 + 70) * MIN - 10_000, 95.0, band=90.0, band_rate=0.0)
        s.run(594, 5)                     # worn, nothing of ours, 5 %/h for almost 10 hours
        s.set(w="DOFFED")
        s.run(70, 0.3)                    # off the face 70 minutes with no change: "under 1% an hour"
        answer(pg, now_of(s), s.out)
        pg.evaluate("span = 'week'; render()")
        rows = view(pg)["rows"]
        fit = pg.evaluate("[...document.querySelectorAll('#usesGlasses li > span')].every((el) => el.scrollWidth <= el.clientWidth + 1)")
        ok = (fit and any(r.startswith("Not worn | over 1 h 10 min | under 1% an hour") for r in rows)
              and any(re.match(r"Worn, anything else \| over 9 h \d+ min \| 5% an hour", r) for r in rows))
        check("D4 the longest texts fit their columns (\"over 9 h 50 min\", \"under 1% an hour\")", ok, [fit, rows])
        close(pg)

    def case_views():
        pg = page_at(URL, connect=True)
        s = normal_day(pg.evaluate("Date.now()"))
        answer(pg, now_of(s), s.out)
        pg.evaluate("ask()")
        pg.wait_for_timeout(100)
        pg.evaluate("asked = Date.now() - 13000; render()")
        shot(pg, "5-not-answering")
        pg.evaluate("asked = 0; render(); lastInputAt = Date.now() - 16000; checkCalm()")
        pg.wait_for_timeout(100)
        shot(pg, "8-calm")
        close(pg)

    def case_files():
        html = fetch("index.html")
        manifest = json.loads(fetch(".well-known/meta-wearables-manifest.json"))
        sw = fetch("sw.js")
        ok = ("and how fast they drain" in html and "When acting for the wearer" not in html
              and "and how fast they drain" in manifest["description"] and "const CACHE = 'battery-glass-v3';" in sw)
        check("D5 the page and launcher descriptions say \"how fast they drain\"; no instruction in the description; cache v3", ok)

    for name, fn in [("D7 no link", case_no_link), ("D7 not enough", case_not_enough), ("D normal day", case_normal_day),
                     ("D6 saved and own", case_saved_and_own), ("D10 charging", case_charging), ("D2 low", case_low),
                     ("D7 band", case_band), ("D3 lost", case_lost), ("D11 gap", case_gap), ("D4 columns", case_columns),
                     ("views", case_views),
                     ("D5 files", case_files)]:
        guarded(name, fn)

    # ---- N, A: the Director's recheck 1 ---------------------------------------------------------------------
    # N11, N11r and N13 use the readings of recheck-1\director\scripts\recheck_harness.py (same Sim, same numbers).

    def case_n11():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = Sim(now_ms - 140 * MIN, 92.0, band=85.0, band_rate=0.8)
        s.set(gc="CHARGING")               # on the charger until 140 min ago
        s.t += 60_000
        s.set(gc="NOT_CHARGING", w="DOFFED")
        s.run(95, 3)                       # on the desk, not worn, 3 %/h
        s.set(w="DONNED", m=True)
        s.run(44, 22)                      # Follow my voice, 22 %/h, still going
        answer(pg, now_of(s), s.out)
        v = view(pg)
        low, empty = true_targets(s.t, s.level, 22)
        got_low, got_empty = int(v["leftData"].get("low", "0")), int(v["leftData"].get("empty", "0"))
        ok = (v["left"].startswith("At this pace: 20% around ") and " · empty around " in v["left"]
              and abs(got_low - low) < 10 * MIN and abs(got_empty - empty) < 10 * MIN
              and near(rate_of(v["rows"], "Follow my voice (glasses mic on)"), 22, 2))
        check("N11 95 min off the face, then 44 min of Follow my voice: the time left is at this pace, within 10 minutes "
              "of the truth at 22% an hour (bccfc7c: 3-4 hours late)",
              ok, [v["left"], (got_low - low) / MIN, (got_empty - empty) / MIN, v["rows"]])
        shot(pg, "N11-at-this-pace")
        close(pg)

    def case_n11r():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = Sim(now_ms - 91 * MIN, 100.0, band=85.0, band_rate=5.0)
        s.set(gc="CHARGING")
        s.t += 60_000
        s.set(gc="NOT_CHARGING", w="DOFFED")
        s.run(60, 25)                      # off, 25 %/h (the coordinator's rough not-worn figure)
        s.set(w="DONNED", a="tidal", p=True)
        s.run(30, 50)                      # Tidal Glass open with music, 50 %/h, still going
        answer(pg, now_of(s), s.out)
        v = view(pg)
        low, empty = true_targets(s.t, s.level, 50)
        got_low, got_empty = int(v["leftData"].get("low", "0")), int(v["leftData"].get("empty", "0"))
        ok = (v["left"].startswith("At this pace: 20% around ") and " · empty around " in v["left"]
              and abs(got_low - low) < 10 * MIN and abs(got_empty - empty) < 10 * MIN)
        check("N11r real-like rates, an hour off at 25% then 30 min of Tidal Glass at 50% an hour: 20% and empty within "
              "10 minutes of the truth (bccfc7c: about 18 and 29 minutes late)",
              ok, [v["left"], (got_low - low) / MIN, (got_empty - empty) / MIN])
        close(pg)

    def case_n13():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = Sim(now_ms - 160 * MIN, 95.0, band=85.0, band_rate=1.5)
        s.run(40, 30)                      # worn, 30 %/h, every percent reported
        s.set(w="DOFFED")
        s.run(90, 20, visible=False)       # IF the toolkit sent no level while they were off (not verified)
        s.shown_g = s.shown()
        s.set(w="DONNED")                  # put on again: the level arrives with the worn state, 30 points lower
        s.run(30, 30)
        answer(pg, now_of(s), s.out)
        v = view(pg)
        ok = (rate_of(v["rows"], "Not worn") is None and not any("under" in r for r in v["rows"])
              and near(rate_of(v["rows"], "Worn, anything else"), 30, 3))
        check("N13 90 min off the face with no level seen, then 30 points lower: no \"Not worn\" row saying \"under 1% an "
              "hour\" (true: 20% an hour, unseen)", ok, v["rows"])
        close(pg)

    def case_n13_band():
        # The band read every 15 minutes: 80% for 90 minutes, then 70%. The span before the drop is not quiet either.
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        t0 = now_ms - 126 * MIN
        h = []
        for i in range(22):                # a line every 6 minutes, on each change of the glasses (10 %/h)
            at = t0 + i * 6 * MIN
            read = t0 + (i * 6 // 15) * 15 * MIN
            h.append({"at": at, "g": 90 - i, "gc": "NOT_CHARGING", "w": "DONNED", "c": True, "a": "", "m": False,
                      "p": False, "b": 80 if read - t0 <= 90 * MIN else 70, "bt": read})
        answer(pg, {k: h[-1][k] for k in ("g", "gc", "w", "c", "b", "bt", "a", "m", "p")}, h)
        v = view(pg)
        check("N13 the band's note gives no \"under X% an hour\" from a span that ended 10 points lower",
              "under" not in v["bandNote"], v["bandNote"])
        close(pg)

    def case_gap_keeps_quiet():
        # A phone that was off for 7 hours, with the level the same on both sides: the quiet hour before still counts.
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        base = {"g": 60, "gc": "NOT_CHARGING", "w": "DOFFED", "c": True, "a": "", "m": False, "p": False}
        h = [{**base, "at": now_ms - 9 * HOUR + i * 20 * MIN, "b": 80 - i} for i in range(4)]  # a line per band change
        h.append({**base, "at": now_ms - 10 * MIN, "b": 72})
        answer(pg, {k: h[-1][k] for k in ("g", "gc", "w", "c", "b", "a", "m", "p")}, h)
        v = view(pg)
        nw = rate_of(v["rows"], "Not worn")
        check("B a gap over 6 hours with the level unchanged keeps the quiet hour before it (\"under 1% an hour\")",
              nw is not None and nw[0] == "under", v["rows"])
        close(pg)

    def case_since_unplugging():
        pg = page_at(URL, connect=True)
        now_ms = pg.evaluate("Date.now()")
        s = Sim(now_ms - 181 * MIN, 90.0, band=80.0, band_rate=0.5)
        s.set(gc="CHARGING")
        s.t += 60_000
        s.set(gc="NOT_CHARGING", a="tidal")
        s.run(60, 16)                      # Tidal Glass, 16 %/h
        s.set(w="DOFFED", a="")
        s.run(120, 1.5)                    # off the face 2 hours, a change every 40 minutes
        answer(pg, now_of(s), s.out)
        v = view(pg)
        ok = v["left"].startswith("At the pace since unplugging: 20% around ") and " · " not in v["left"]
        check("A fewer than 3 changes in the last 30 minutes: the pace since unplugging, said so, with one time", ok, v["left"])
        shot(pg, "13-since-unplugging")
        close(pg)

    def reconnect_day(now_ms):
        s = Sim(now_ms - (60 + 20 + 150) * MIN - 10_000, 100.0, band=92.0, band_rate=1.0)
        s.set(a="tidal")
        s.run(60, 18)
        s.set(c=False)
        s.run(20, 2, visible=False)        # the phone loses the glasses for 20 minutes
        s.t += 2000
        s.f.update(c=True, w="DOFFED", gc="UNKNOWN", a="")
        s.emit(g=0)                        # the toolkit's 0 with charging unknown
        s.t += 5000
        s.f.update(gc="NOT_CHARGING")
        s.shown_g = s.shown()
        s.emit()
        start = s.t
        s.run(150, 2)                      # not worn since, 2 %/h
        return s, start

    def case_since_reconnect():
        pg = page_at(URL, connect=True)
        s, start = reconnect_day(pg.evaluate("Date.now()"))
        answer(pg, now_of(s), s.out)
        v = view(pg)
        at = clock_of(pg, start)
        # Changed on purpose (Director recheck 2, A2, in the coordinator's fallback order): fewer than 3 changes in the
        # last 30 minutes, and today's list has a "Not worn" row (2% an hour over the 2 hours since the phone found them
        # again), so today's rate for not worn comes before the stretch's pace. 3b127b9 said "At the pace since …" here;
        # the variant below still reaches that line.
        ok = (v["left"].startswith("20% around ") and v["left"].endswith(" if you leave them off")
              and "unplugging" not in v["left"])
        check("A after the phone lost the glasses and they stayed off: today's rate for not worn comes first, "
              "\"... if you leave them off\", never since unplugging", ok, [v["left"], at])
        close(pg)

    def case_since_reconnect_put_on():
        pg = page_at(URL, connect=True)
        s, start = reconnect_day(pg.evaluate("Date.now()"))
        s.set(w="DONNED")                  # put on just before the answer; no row today for being worn
        answer(pg, now_of(s), s.out)
        v = view(pg)
        at = clock_of(pg, start)
        ok = v["left"].startswith(f"At the pace since {at}: 20% around ") and "unplugging" not in v["left"]
        check("A after the phone lost the glasses, put on just before the answer with no row today for being worn: the "
              "pace is said since the time the phone found them again, not since unplugging", ok, [v["left"], at])
        close(pg)

    def case_line_fits():
        pg = page_at(URL, connect=True)
        longest = ["At this pace: 20% around 12:30 AM · empty around 12:30 AM",
                   "At this pace: 20% around Wed 12:30 AM",
                   "At the pace since unplugging: 20% around Wed 12:30 AM",
                   "At the pace since unplugging: empty around Wed 12:30 AM",
                   "At the pace since Wed 12:30 AM: 20% around Wed 12:30 AM",
                   "At the pace since Wed 12:30 AM: empty around Wed 12:30 AM",
                   "20% around Wed 12:30 AM if Follow my voice stays on",
                   "Empty around Wed 12:30 AM if Follow my voice stays on",
                   "Phone app lost the glasses at Wed 12:30 AM"]
        measure_js = """(texts) => {
            const el = document.getElementById('left');
            const keep = el.textContent;
            const out = texts.map((t) => { el.textContent = t; return [t, Math.round(el.getBoundingClientRect().height)]; });
            el.textContent = keep;
            return out;
        }"""
        res = {"page font": pg.evaluate(measure_js, longest)}
        pg.add_style_tag(content="html, body, button { font-family: Inter, sans-serif !important; }")
        pg.wait_for_timeout(200)
        res["Inter (wider)"] = pg.evaluate(measure_js, longest)
        check("A the longest time lines stay on one line, in the page's font and in the wider Inter",
              all(hgt <= ONE_LINE for rows in res.values() for _, hgt in rows), res)
        close(pg)

    for name, fn in [("N11", case_n11), ("N11r", case_n11r), ("N13", case_n13), ("N13 band", case_n13_band),
                     ("B gap", case_gap_keeps_quiet), ("A since unplugging", case_since_unplugging),
                     ("A since reconnect", case_since_reconnect), ("A since reconnect, put on", case_since_reconnect_put_on),
                     ("A line fits", case_line_fits)]:
        guarded(name, fn)

    # ---- R, H: the Director's recheck 2 -----------------------------------------------------------------------
    # Must-fix A2: "At this pace" counts only the level changes since the situation last changed (worn or not, the
    # glasses app in use, Follow my voice, Tidal playing) that are also in the last 30 minutes, 3 at least. The readings
    # are those of recheck-2\director\scripts\recheck2_harness.py (c_r2 from 90.5%, c_r2h, c_r1), written with this
    # file's Sim. The harness's Sim also writes p = false and the band's read time from the first reading; p is off until
    # the switch either way, and the time line does not read the band.

    def r2_day(now_ms, on_min):
        """c_r2, start 90.5: 2 h off the face at 2% an hour (the last slow change 15 min before the switch), then
        on_min of Tidal Glass with music at 30% an hour, still going."""
        s = Sim(now_ms - (1 + 120 + on_min) * MIN, 90.5, band=85.0, band_rate=1.0)
        s.set(gc="CHARGING")
        s.t += 60_000
        s.set(gc="NOT_CHARGING", w="DOFFED")
        s.run(120, 2)
        s.set(w="DONNED", a="tidal", p=True)
        s.run(on_min, 30)
        return s

    def r2h_day(now_ms, on_min):
        """c_r2h, real-like rates: an hour off at 25% an hour, then on_min of Tidal Glass with music at 50%."""
        s = Sim(now_ms - (1 + 60 + on_min) * MIN, 100.0, band=85.0, band_rate=5.0)
        s.set(gc="CHARGING")
        s.t += 60_000
        s.set(gc="NOT_CHARGING", w="DOFFED")
        s.run(60, 25)
        s.set(w="DONNED", a="tidal", p=True)
        s.run(on_min, 50)
        return s

    def r1_day(now_ms, off_min):
        """c_r1: an hour of Follow my voice at 22% an hour, then off the face for off_min at 3%."""
        s = Sim(now_ms - (1 + 60 + off_min) * MIN, 95.0, band=85.0, band_rate=1.0)
        s.set(gc="CHARGING")
        s.t += 60_000
        s.set(gc="NOT_CHARGING", w="DONNED", m=True)
        s.run(60, 22)
        s.set(w="DOFFED", m=False)
        s.run(off_min, 3)
        return s

    def r_view(build, shot_name=None):
        pg = page_at(URL, connect=True)
        s = build(pg.evaluate("Date.now()"))
        answer(pg, now_of(s), s.out)
        v = view(pg)
        if shot_name:
            shot(pg, shot_name)
        close(pg)
        return s, v

    def r_times(name, build, rate, shot_name=None):
        """At this pace, both times within 10 minutes of a level falling at `rate` from now, rounded to 5 minutes."""
        s, v = r_view(build, shot_name)
        low, empty = true_targets(s.t, s.level, rate)
        got_low, got_empty = int(v["leftData"].get("low", "0")), int(v["leftData"].get("empty", "0"))
        ok = (v["left"].startswith("At this pace: 20% around ") and " · empty around " in v["left"]
              and abs(got_low - low) < 10 * MIN and abs(got_empty - empty) < 10 * MIN
              and all(m % 5 == 0 for m in clock_minutes(v["left"])))
        check(name, ok, [v["left"], (got_low - low) / MIN, (got_empty - empty) / MIN])

    def case_r2e():
        r_times("R2e 6 min of Tidal Glass with music at 30% an hour after 2 h off the face, the last slow change 15 min "
                "before the switch: \"At this pace\" is the music's pace, 20% and empty within 10 minutes of the truth "
                "(3b127b9: about 5 and 6.5 hours late)", lambda now_ms: r2_day(now_ms, 6), 30, "R2e-at-this-pace-after-switch")

    def case_r2f():
        r_times("R2f the same switch 10 min in: \"At this pace\" within 10 minutes of the truth at 30% an hour "
                "(3b127b9: about 3 and 4 hours late)", lambda now_ms: r2_day(now_ms, 10), 30)

    def case_r2h6():
        r_times("R2h6 real-like rates, an hour off at 25% then 6 min of Tidal Glass with music at 50% an hour: \"At this "
                "pace\" within 10 minutes of the truth (3b127b9: about 39 and 54 minutes late)", lambda now_ms: r2h_day(now_ms, 6), 50)

    def case_r2d():
        _, v = r_view(lambda now_ms: r2_day(now_ms, 4), "R2d-fallback-after-switch")
        check("R2d the same switch 4 min in, only 2 changes since it: the line does not say \"At this pace\" "
              "(3b127b9: \"At this pace\", 7.5 hours late)", "At this pace" not in v["left"], v["left"])

    def case_r1():
        seen = []
        for off in (12, 25, 35):
            _, v = r_view(lambda now_ms, off=off: r1_day(now_ms, off))
            seen.append([off, v["left"]])
        check("R1 an hour of Follow my voice, then taken off 12, 25 and 35 min ago: never \"At this pace\", so never a "
              "blend under that label (3b127b9: the heavy pace at 12 min, a blend about 7 hours off at 25)",
              all("At this pace" not in left for _, left in seen), seen)

    # Should-fix H: Tidal Glass build 17's scheduled retries (its webapp\tests\link_test.py check 7), and none while
    # hidden: the link closes 400 ms after the page is hidden, before a retry due 1 s after a close.
    def case_retries():
        pg = page_at(URL)
        d = pg.evaluate("[1, 2, 3, 4, 5, 6, 8, 12].map((n) => retryDelay(n, false)).concat([retryDelay(2, true), retryDelay(12, true)])")
        opts = pg.evaluate("window.__clients.map((c) => [c.opts.reconnectPeriod, c.opts.connectTimeout])")
        pg.evaluate("window.__clients[0].fire('close')")     # a failed try with nothing connected: the next 1 s later
        pg.wait_for_timeout(1300)
        tried = pg.evaluate("window.__clients[0].tries || 0")
        pg.evaluate("window.__clients[1].fire('close')")     # a retry now due in 1 s, then the page is hidden
        pg.evaluate("""() => {
            Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
            document.dispatchEvent(new Event('visibilitychange'));
        }""")
        pg.wait_for_timeout(1500)
        hidden = pg.evaluate("[links.length, window.__clients.map((c) => c.tries || 0), window.__clients.length]")
        close(pg)
        ok = (d == [1000, 1000, 1000, 2000, 4000, 8000, 30000, 30000, 4000, 600000] and opts == [[0, 6000]] * 3
              and tried == 1 and hidden == [0, [1, 0, 0], 3])
        check("H retries back off as Tidal Glass build 17's (mqtt.js's own reconnect off, 6 s connect timeout; 1 s x3, "
              "then up to 30 s, 10 min while another broker works), and a hidden page's closed link tries nothing",
              ok, [d, opts, tried, hidden])

    for name, fn in [("R2e", case_r2e), ("R2f", case_r2f), ("R2h6", case_r2h6), ("R2d", case_r2d), ("R1", case_r1),
                     ("H", case_retries)]:
        guarded(name, fn)

    # ---- P: the Photographer's recheck 1 ----------------------------------------------------------------------

    def case_calm_warning():
        pg = page_at(URL, connect=True)
        s = normal_day(pg.evaluate("Date.now()"))
        answer(pg, now_of(s), s.out)
        pg.evaluate("ask()")
        pg.wait_for_timeout(100)
        pg.evaluate("asked = Date.now() - 13000; render(); lastInputAt = Date.now() - 16000; checkCalm()")
        pg.wait_for_timeout(100)
        a = pg.evaluate("""[document.body.classList.contains('calm'), document.getElementById('state').classList.contains('warn'),
            __eff('#state'), getComputedStyle(document.getElementById('state')).color, __eff('#glasses .pct'), __eff('#band .pct')]""")
        shot(pg, "9-not-answering-calm")
        pg.evaluate("asked = 0; render()")
        pg.wait_for_timeout(100)
        b = pg.evaluate("[document.body.classList.contains('calm'), document.getElementById('state').classList.contains('warn'), __eff('#state')]")
        ok = a[0] and a[1] and a[2] == 1 and a[3] == AMBER and a[2] > max(a[4], a[5]) and b[0] and not b[1] and b[2] < 1
        check("P-A in calm the \"not answering\" warning keeps its full strength, above the old numbers; a plain top line "
              "still dims", ok, [a, b])
        close(pg)

    def case_bars():
        seen = []
        for name, g, band in (("glasses 100%", 100, None), ("glasses 9%", 9, None), ("glasses 35%", None, None),
                              ("band, no permission", None, "no-permission"), ("band, no battery service", None, "no-battery-service")):
            pg = page_at(URL, connect=True)
            s = normal_day(pg.evaluate("Date.now()"))
            now = now_of(s)
            h = s.out
            if g is not None:
                now["g"] = g
            if band:
                now.update(b=-1, bw=band)
                h = [{**r, "b": -1} for r in s.out]
            answer(pg, now, h)
            m = pg.evaluate("""() => ['glasses', 'band'].map((id) => {
                const box = (sel) => document.querySelector(`#${id} ${sel}`).getBoundingClientRect();
                const level = document.getElementById(id).getBoundingClientRect();
                return { id, level: Math.round(level.width), bar: Math.round(box('.bar').width),
                         gap: Math.round(box('.pct').left - box('.name').right), pct: document.querySelector(`#${id} .pct`).textContent };
            })""")
            seen.append([name, m])
            if g == 100:
                shot(pg, "10-glasses-100")
            if band == "no-permission":
                shot(pg, "11-band-no-permission")
            close(pg)
        bars = [x["bar"] for _, m in seen for x in m]
        ok = (all(abs(x["bar"] - x["level"]) <= 1 and 0 <= x["gap"] <= 16 for _, m in seen for x in m)
              and max(bars) - min(bars) <= 1)
        check("P-B each bar runs its whole column (glasses at 100%, 35% and 9%; band beside its longest reasons); "
              "the number and the dash sit just after their names", ok, seen)

    def case_age_note():
        pg = page_at(URL, connect=True)
        s = normal_day(pg.evaluate("Date.now()"))
        answer(pg, now_of(s, bt=s.t - 3 * HOUR), s.out)
        a = pg.evaluate("""[document.getElementById('bandNote').textContent, document.getElementById('band').classList.contains('old'),
            __eff('#bandNote'), __eff('#band .pct')]""")
        shot(pg, "12-band-read-3h")
        close(pg)
        check("P-C \"Read 3 h ago\" keeps its full strength while the old band number dims",
              a[0] == "Read 3 h ago" and a[1] and a[2] == 1 and a[3] < 1, a)
        # The Photographer's S05: with the phone not answering, the band's rate note stays as dim as its old number.
        pg = page_at(URL, connect=True)
        s = normal_day(pg.evaluate("Date.now()"))
        answer(pg, now_of(s), s.out)
        pg.evaluate("ask()")
        pg.wait_for_timeout(100)
        pg.evaluate("asked = Date.now() - 13000; render()")
        b = pg.evaluate("[document.getElementById('bandNote').textContent, __eff('#bandNote'), __eff('#band .pct')]")
        close(pg)
        check("P-C with the phone not answering, the band's rate note is no brighter than its old number",
              b[0].startswith("Uses ") and b[1] <= b[2] < 1, b)

    for name, fn in [("P-A", case_calm_warning), ("P-B", case_bars), ("P-C", case_age_note)]:
        guarded(name, fn)

    check("A the time-left line never wraps onto a second line, in any case above", not wraps, wraps)
    check("no page errors", not errors, "; ".join(errors))
    browser.close()

print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
