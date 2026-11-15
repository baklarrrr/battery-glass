// Battery log: keeps the glasses' own battery readings, so Battery can show a history without any phone.
// Include from any app on this origin with one line (data-app is the name Battery shows):
//   <script src="/battery-glass/battery-log.js" data-app="Tidal Glass"></script>
// No dependencies, no network. Readings stay in this browser's storage: [time, level 0-100, charging 0/1, app].
// (A fifth item, 1, marks a reading that only says the level before it still held.)
// Kept 7 days, 2000 readings at most. A reading is taken on opening, on a change of level or charging, when the
// page is shown or hidden, and every 4 minutes while it is on screen. Nothing here ever throws.
(function () {
  'use strict';
  var KEY = 'bakar.battery.log'; // one store for every app on this origin
  var KEEP = 7 * 24 * 3600000;
  var MAX = 2000;
  var HELD = 15 * 60000; // readings further apart than this are a gap: nobody was reading
  var EVERY = 4 * 60000;
  var me = document.currentScript;
  var app = String((me && me.getAttribute('data-app')) || document.title || 'App').slice(0, 24);

  function add(level, charging) {
    try {
      var now = Date.now();
      var log = JSON.parse(localStorage.getItem(KEY) || '[]');
      log = (Array.isArray(log) ? log : []).filter(function (r) { return Array.isArray(r) && r[0] > now - KEEP && r[0] <= now; });
      var a = log[log.length - 1];
      var held = a && a[1] === level && a[2] === charging && a[3] === app && now - a[0] <= HELD;
      if (held && now - a[0] < 1000) return; // the same reading twice at once
      // While nothing changes, two readings say "held from, until": the second (marked 1) only moves forward in time.
      if (held && a[4]) a[0] = now;
      else log.push(held ? [now, level, charging, app, 1] : [now, level, charging, app]);
      localStorage.setItem(KEY, JSON.stringify(log.slice(-MAX)));
      window.dispatchEvent(new Event('batterylog'));
    } catch (e) { /* storage full, off or unreadable: this reading is not kept */ }
  }

  try {
    // Only the glasses: a phone or laptop tab would log its own battery as the glasses'.
    if (!/\bGreatwhite\b/i.test(navigator.userAgent) || !navigator.getBattery) return;
    navigator.getBattery().then(function (b) {
      var read = function () {
        // Chromium's answer when it has no battery data at all (full, charging, never empties) is not a reading.
        if (b.level === 1 && b.charging && b.chargingTime === 0 && b.dischargingTime === Infinity) return;
        if (typeof b.level !== 'number' || !(b.level >= 0 && b.level <= 1)) return;
        add(Math.round(b.level * 100), b.charging ? 1 : 0);
      };
      var seen = function () { if (document.visibilityState === 'visible') read(); };
      seen();
      b.addEventListener('levelchange', seen);
      b.addEventListener('chargingchange', seen);
      document.addEventListener('visibilitychange', read); // also the last reading as the page goes off screen
      setInterval(seen, EVERY);
    }).catch(function () { /* no battery to read */ });
  } catch (e) { /* never break the app that includes this */ }
})();
