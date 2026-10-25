"""Battery (glasses app) tests, with the MQTT library replaced by a fake and the phone played by the test.

Serves nothing itself: start `python -m http.server 47641 --bind 127.0.0.1 --directory webapp` first.
  python webapp/tests/battery_test.py [base_url] [shots_dir]

1. Without a pairing in the address (or stored), the page says how it gets linked.
2. All three brokers open together; the first connect sends one sealed batHistory for the last 24 hours.
3. A broker that connects while the request is unanswered gets the same request.
4. The answer draws both levels, the time left, what uses the glasses (highest first) and both lines.
5. A low level (20% or less, not charging) is amber; charging is green and says so.
6. No answer for 12 s: the top line says the phone is not answering, in the warning look.
7. The span button asks again for 7 days.
8. Calm after 15 s dims the details but not the levels.
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:47641/").rstrip("/") + "/"
SHOTS = Path(sys.argv[2]) if len(sys.argv) > 2 else None
URL = f"{BASE}index.html?test=1#t=bgtest&k={KEY}"
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


failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(name)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", args=["--enable-spatial-navigation"])
    errors = []

    def page_at(url):
        pg = browser.new_context(viewport={"width": 600, "height": 600}).new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.route("**/vendor/mqtt.min.js", lambda r: r.fulfill(status=200, content_type="application/javascript", body=FAKE_MQTT))
        pg.goto(url)
        return pg

    def shot(pg, name):
        if SHOTS:
            SHOTS.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(SHOTS / f"{name}.png"))

    # 1. Unpaired
    pg = page_at(f"{BASE}index.html?test=1")
    pg.wait_for_timeout(400)
    check("1 without a pairing the page says how it gets linked",
          pg.evaluate("!document.getElementById('unpaired').hidden && document.getElementById('main').hidden"))
    shot(pg, "1-unpaired")
    pg.close()

    # 2-8 Paired
    pg = page_at(URL)
    pg.wait_for_function("window.__clients && window.__clients.length === 3")
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

    def answer(now_levels, history):
        pg.evaluate("""async ([nowLevels, h]) => {
            const body = await seal({ t: 'bat', re: asked, at: Date.now(), now: nowLevels, h });
            await handle(body);
        }""", [now_levels, history])
        pg.wait_for_timeout(200)

    now_ms = pg.evaluate("Date.now()")
    hist = day_of_readings(now_ms)
    last = hist[-1]
    answer({"g": last["g"], "gc": "NOT_CHARGING", "w": "DONNED", "c": True, "b": last["b"]}, hist)
    view = pg.evaluate("""() => ({
        g: document.querySelector('#glasses .pct').textContent, b: document.querySelector('#band .pct').textContent,
        left: document.getElementById('left').textContent,
        rows: [...document.querySelectorAll('#usesGlasses li')].map((li) => li.textContent),
        bandNote: document.getElementById('bandNote').textContent,
        gPoints: document.getElementById('lineGlasses').getAttribute('points').split(' ').filter(Boolean).length,
        bPoints: document.getElementById('lineBand').getAttribute('points').split(' ').filter(Boolean).length,
        state: document.getElementById('stateText').textContent })""")
    ok4 = (view["g"] == f"{last['g']}%" and view["b"] == f"{last['b']}%" and "left" in view["left"]
           and view["rows"] and view["rows"][0].startswith("Worn, Quran listening") and view["gPoints"] > 50 and view["bPoints"] > 50
           and view["state"].startswith("From your phone"))
    check("4 the answer draws both levels, time left, what uses it (highest first) and both lines", ok4, json.dumps(view)[:400])
    shot(pg, "2-day")

    # 5. low and charging
    answer({"g": 14, "gc": "NOT_CHARGING", "w": "DONNED", "c": True, "b": 9}, hist)
    low = pg.evaluate("[document.getElementById('glasses').classList.contains('low'), document.getElementById('band').classList.contains('low'), getComputedStyle(document.querySelector('#glasses .pct')).color]")
    shot(pg, "3-low")
    answer({"g": 44, "gc": "CHARGING", "w": "DOFFED", "c": True, "b": 60}, hist)
    chg = pg.evaluate("[document.getElementById('glasses').classList.contains('charging'), document.getElementById('glassesNote').textContent, document.getElementById('left').textContent]")
    check("5 a low level is amber; charging is green and says so", low[0] and low[1] and low[2] == "rgb(255, 176, 138)" and chg[0] and chg[1] == "Charging" and chg[2] == "Charging now", json.dumps([low, chg]))
    shot(pg, "4-charging")

    # 6. not answering
    pg.evaluate("ask()")
    pg.wait_for_timeout(100)
    pg.evaluate("asked = Date.now() - 13000; render()")
    warn = pg.evaluate("[document.getElementById('stateText').textContent, document.getElementById('state').classList.contains('warn')]")
    check("6 no answer for 12 s: the phone is not answering, in the warning look", warn[0].startswith("Your phone is not answering") and warn[1], warn)
    shot(pg, "5-not-answering")

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
    shot(pg, "6-calm")

    check("no page errors", not errors, "; ".join(errors))
    browser.close()

print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
