# Battery

A glasses app for Meta Ray-Ban Display: the glasses' and the band's battery now, when to charge, and how fast the
glasses drain in each situation the phone can see, worked out from real readings.

## Where the numbers come from

- **The glasses' level and charging** come first from the glasses themselves (`navigator.getBattery()` in the glasses'
  browser), so they show even with no link to the phone. Chromium's "no data" answer (full, charging, never empties)
  is ignored, as in Quran Glass. The phone's report is the fallback.
- **The band's level and the history** come from the Tidal Glass app on the paired phone (`BatteryWatch.kt`): the
  glasses' level, charging, worn state and link from Meta's Wearables toolkit; the band's level from its standard
  Bluetooth battery service, with the time it was read (`bt`) and, when it can't be read, why (`bw`); which of these
  glasses apps is open (Tidal Glass, Sound Lab, Quran Glass); Follow my voice; and Tidal playing (`p`).

Every change appends one line, kept 7 days on the phone. This page asks for them over the phone's sealed link
(`batHistory` on `bkr/<topic>/c`, answered on `bkr/<topic>/bat`) when it opens and every 5 minutes while on screen;
after the first answer it asks only for readings newer than the newest it holds, and merges them. The last answer is
kept on the glasses (per pairing) and drawn at once on the next launch, marked with its time, while the link connects.
The phone's state on `bkr/<topic>/s` fills in the levels until the phone answers; a broker's stored copy (MQTT
retained) is shown with its time and never replaces what the phone said on this visit.

All three brokers are opened together, and the page schedules each one's retries itself, as Tidal Glass build 17 does
(mqtt.js's own reconnect is off; connect timeout 6 s): 1 s for the first three tries, then doubling to 30 s while
nothing is connected, and up to 10 minutes for a broker that keeps failing while another carries the link. The link
closes 400 ms after the page is hidden, and closing it clears every pending retry, so from then on nothing is tried (a
retry already due in those 400 ms can still go out).

## How the numbers are worked out

- **Tick to tick.** Levels move in whole percents, so a rate is measured from one change of the level to another,
  never from a single change.
- **Only readings the phone could vouch for.** A step between two readings is skipped when either was disconnected,
  had charging or wearing unknown, or when the level fell more than 2 points between them (a reconnect, or a phone
  that was off). This assumes the toolkit reports every whole percent, which is not yet checked on the real glasses.
- **How fast they drain** lists a situation only when the time between two changes stayed in that one situation, with
  at least 3 points over 30 minutes; rates are whole numbers. A situation with no fall at all over h hours says
  "under X% an hour" (X = 1/h, rounded up), never 0. A span that ends in a jump or a step the wrong way never counts
  as no fall: the level moved where the phone could not see it. (A gap of over 6 hours with no jump still counts the
  span before it.) The band's note follows the same rules, timed by when the band was read, so a band the phone has
  stopped reading adds no hours.
- **Situations:** Not worn; Follow my voice (the glasses' microphone on; Quran Glass's own commands come only then);
  Tidal Glass open; Sound Lab open; Music playing; Worn, anything else.
- **When to charge** is said as clock times, from the current stretch off the charger (it starts after the last
  reading that was charging, unknown or disconnected, or where the readings break: a jump, a rise, or 6 hours with no
  reading), and the line says which pace it uses:
  - "At this pace: 20% around 9:30 PM · empty around 11:30 PM" counts only the changes of the level since the
    situation last changed (worn or not, the glasses app in use, Follow my voice, Tidal playing) that are also in the
    last 30 minutes, 3 at least, so it never blends two situations. The empty time is shown only when it needs no day
    name, so the line stays on one line;
  - fewer: today's rate for what is going on now, when today's list has a row for it, "20% around 9:30 PM if Tidal
    Glass stays open";
  - else the pace over the whole stretch, with 3 changes at least, "At the pace since unplugging: 20% around 5:30 AM",
    or "since 6:45 PM" when the stretch did not start at the charger (the phone lost the glasses, or saw a jump). This
    line can span a change of situation, and its label says what it measures.

  The phone's word on worn, the app in use and Follow my voice counts too: when it differs from the last reading, the
  situation changed after it. Tidal playing is taken from the readings only, because the phone's live state blinks off
  between songs, so after music starts the line can keep the earlier pace until the next answer (the page asks every
  5 minutes). Times are rounded to 5 minutes within the hour, 15 within 3 hours, 30 beyond. While charging: "Full
  around …" from this charge's own readings, or nothing.
- **The chart** breaks where the readings stop being continuous (a jump of more than 2 points, or over 30 minutes of
  unknown or disconnected readings). The phone writes a line only on a change, so a long quiet stretch stays joined.

Not counted, because the phone cannot see them: Meta's own apps, the display's brightness, the radios, Quran Glass
outside Follow my voice, and Bakar Studio or Bakar Home being open (they talk to the PC, not the phone). One dim line
under the list names Meta's apps, brightness and Quran Glass outside Follow my voice.

## Adding it to the glasses

Battery is added to the glasses from the Tidal Glass phone app, with the pairing in its address. The installed phone
build (2026-10-25 18:59) adds it through an "Add Battery to your glasses" notification that the coordinator sends; the
next build also has a "Put Battery on my glasses" button. The unlinked screen says only what is true on both: "It is
added from the Tidal Glass app on your phone, which links it." After the next phone install, the button's name can go
back into that hint, with check D11 changed to match.

## Privacy

The page code is public; nothing private is in it. The pairing (topic and AES-GCM key) reaches the glasses only in
the app's address fragment, which never goes to a server, and every message is sealed with that key, so the public
MQTT brokers (EMQX, HiveMQ, shiftr.io, all opened together) carry only ciphertext. The last answer kept on the glasses
stays in this app's own storage there.

## Checks

`python -m http.server 47641 --bind 127.0.0.1 --directory webapp`, then
`python webapp/tests/battery_test.py [base_url] [shots_dir]` (set `BATTERY_TEST_VERBOSE=1` to print what each check
saw). The test fakes the brokers and the glasses' own battery, blocks service workers (a controlled page would load the
real MQTT library), and plays the phone, so no message leaves the PC. It includes the Director's harness cases
(B01-B05, T1-T3), which fail on 78e18cc, the first rechecks' cases (Director N11, N11r, N13; Photographer A-C), which
fail on bccfc7c, and the Director's second recheck (R2d, R2e, R2f, R2h6 and R1 for "At this pace", H for the
retries), which fail on 3b127b9. With that recheck, D2 and the first since-reconnect check changed on purpose (no "At
this pace" just after a change; today's rate before the pace since unplugging), and so did D11 with the unlinked
hint; each check says why. Captures are drawn with greyscale text (`--disable-lcd-text`), as on the glasses.
