"""Battery (glasses app) tests. The MQTT library and the glasses' own battery are replaced by fakes and the phone is
played by the test, so no message leaves the PC. Service workers are blocked in every browser context: a page that a
service worker controls would fetch the real MQTT library and reach the real brokers.

Serves nothing itself: start `python -m http.server 47641 --bind 127.0.0.1 --directory webapp` first.
  python webapp/tests/battery_test.py [base_url] [shots_dir]

1-8   The first build's checks. Where the Director's review (W:\\Code\\GlassesProgram\\reviews\\battery-glass-v1\\
      director\\NOTES.md) changed wording or structure on purpose, the check says so.
B, T  The Director's harness cases (director\\scripts\\director_harness.py, its Sim class copied below), as checks
      that fail on 78e18cc.
D     The rest of the Director's must-fix items 1-7 and should-fix items 9-11 for the page.
"""
import json
import math
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


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({str(detail)[:600]})" if detail and not ok else ""))
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


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", args=["--enable-spatial-navigation"])
    errors = []

    def new_context():
        ctx = browser.new_context(viewport={"width": 600, "height": 600}, service_workers="block", locale="en-US",
                                  timezone_id="America/Toronto")
        ctx.add_init_script(FAKE_BATTERY)
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
        return pg.evaluate(VIEW)

    def close(pg):
        pg.context.close()

    # ---- 1. Unpaired --------------------------------------------------------------------------------------
    pg = page_at(f"{BASE}index.html?test=1")
    pg.wait_for_timeout(400)
    check("1 without a pairing the page says how it gets linked",
          pg.evaluate("!document.getElementById('unpaired').hidden && document.getElementById('main').hidden"))
    hint = pg.evaluate("document.querySelector('#unpaired .hint').textContent")
    check("D11 the unlinked screen points at the phone's step \"Put Battery on my glasses\"",
          "Tidal Glass app" in hint and "Put Battery on my glasses" in hint, hint)
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
    warn.append(pg.evaluate("[document.getElementById('main').classList.contains('stale'), getComputedStyle(document.getElementById('glasses')).opacity, getComputedStyle(document.getElementById('band')).opacity]"))
    # Changed on purpose (Director item 6): each level from the phone dims on its own, so a level the glasses read
    # themselves can stay bright; the check reads the two levels, not their shared row.
    check("6 no answer for 12 s: the phone is not answering, in the warning look, and the last word dims",
          warn[0].startswith("Your phone is not answering") and warn[1] and warn[2][0] and float(warn[2][1]) < 1 and float(warn[2][2]) < 1, warn)

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
        check("D2 the time left is a clock time for 20% and for empty, from this stretch, rounded",
              v["left"].startswith("20% around ") and " · empty around " in v["left"] and len(mins) == 2
              and all(m % 5 == 0 for m in mins) and v["glassesNote"] == "", [v["left"], v["glassesNote"]])
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
        ok = (v["glasses"] == f"{s.now()['g']}%" and "low" in v["glassesCls"].split() and v["left"].startswith("Empty around ")
              and " · " not in v["left"])
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
                     ("D7 band", case_band), ("D3 lost", case_lost), ("D11 gap", case_gap), ("views", case_views),
                     ("D5 files", case_files)]:
        guarded(name, fn)

    check("no page errors", not errors, "; ".join(errors))
    browser.close()

print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
