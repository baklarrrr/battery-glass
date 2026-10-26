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
  glasses apps is open (Tidal Glass, Sound Lab, Quran Glass); Follow my voice (`m`); and Tidal playing (`p`).
  Readings from older phone builds, without `bt`, `bw` or `p`, still work.
- **Why the band is unknown** (`bw`), one line under it: "Phone app needs Bluetooth access" (`no-permission`), "Not
  paired with your phone" (`not-paired`), "Doesn't share its battery level" (`no-battery-service`), "Bluetooth is off on
  your phone" (`bt-off`), "Not read yet" (`not-read-yet`, or no reason given), and "Your phone can't reach it"
  (`out-of-range`: the phone tried and the band did not answer, whether it is out of range, off or flat; older phone
  builds also sent it with Bluetooth off and before the first read, which these words cover too).

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
  stopped reading adds no hours. Until a row can stand, the list says "Not enough readings yet: each use needs about
  30 minutes in all": the time is added up over the span, so short spells of one use count together, and it is time
  in that use, not time off the charger.
- **Situations:** Not worn; Follow my voice; Tidal Glass open; Sound Lab open; Tidal playing; Worn, anything else.
  - **Follow my voice** counts while the phone says it is listening (`m`), which the phone app ends when Follow my
    voice stops. It names no microphone: the phone listens through the glasses' microphone when it can reach it and
    through its own otherwise, and a reading does not say which. Quran Glass's own "in use" is not folded in: it lasts
    4 minutes after its last message, so it would count minutes after Follow my voice stopped. Quran Glass open
    without Follow my voice falls under what else is going on, as the note under the list says.
  - **Tidal playing** is Tidal's own playing state on the phone; audio from other apps is not seen, so the row does not
    say "music".
- **When to charge** is said as clock times, from the current stretch off the charger (it starts after the last
  reading that was charging, unknown or disconnected, or where the readings break: a jump, a rise, or 6 hours with no
  reading), and the line says which pace it uses:
  - "At this pace: 20% around 9:30 PM · empty around 11:30 PM" counts only the changes of the level since the
    situation last changed (worn or not, the glasses app in use, Follow my voice, Tidal playing) that are also in the
    last 30 minutes, 3 at least, spanning 5 minutes at least, so it never blends two situations and three quick
    changes do not set it: the phone sees whole percents, at uneven times. An off-the-face spell under a minute
    between two worn readings is not a change of situation: real readings have spells of a few seconds that look like
    the wear sensor, and each would start the pace again. The empty time is shown only when it needs no day name, so
    the line stays on one line;
  - fewer: today's rate for what is going on now, when today's list has a row for it, "20% around 9:30 PM if Tidal
    Glass stays open";
  - else the pace over the whole stretch, with 3 changes at least, "At the pace since unplugging: 20% around 5:30 AM",
    or "since 6:45 PM" when the stretch did not start at the charger (the phone lost the glasses, or saw a jump). This
    line can span a change of situation, and its label says what it measures. Just after a real change of use it
    stays up for at least the first 5 minutes of the new use, longer when the level falls slowly.

  The phone's word on worn, the app in use and Follow my voice counts too: when it differs from the last reading, the
  situation changed after it. Tidal playing is taken from the readings only, because the phone's live state blinks off
  between songs, so after music starts the line can keep the earlier pace until the next answer (the page asks every
  5 minutes). Times are rounded to 5 minutes within the hour, 15 within 3 hours, 30 beyond.
  - **An old level gives no time.** Nothing is worked out from a level the phone vouched for more than 30 minutes ago
    (a copy kept from an earlier visit, a phone that stopped answering), unless the glasses read their own battery;
    the line goes when the level turns 30 minutes old, even on screen. A younger one is worked out from the time of the
    phone's word, not from now, and the top line dates it ("levels from 9:40 PM").
  - **While charging** the line gives the time for 80% ("80% around 11:20 PM"), from this charge's own readings, and
    nothing from 80% on. A lithium battery charges at a steady rate for most of the way and slows near full; how much
    these glasses slow down has not been measured, so a straight line to 100% would be early by an unknown margin.
    The time for full can come back once a real charge to 100% has been read.
- **The chart** breaks where the readings stop being continuous (a jump of more than 2 points, or over 30 minutes of
  unknown or disconnected readings). The phone writes a line only on a change, so a long quiet stretch stays joined. A
  reading alone, often the level now after a break, is a 3 px dot. The legend names only the lines drawn.
- **On the display.** After 15 s without input (calm), the list's names and rates, the chart and the legend's swatches
  dim, and so does the top line while it is news; text already grey keeps its strength. When the phone's word is not
  news (stale), the time line, the list's names and rates, the chart and the legend's swatches, and the phone's levels
  dim; the top line that dates them does not, and it turns into the amber warning once nothing has answered, or the
  link has been down, for 12 s. With no link and nothing kept, the list shows nothing, not even its heading.

Not counted, because the phone cannot see them: Meta's own apps, the display's brightness, the radios, Quran Glass
outside Follow my voice, and Bakar Studio or Bakar Home being open (they talk to the PC, not the phone). One dim line
under the list names Meta's apps, brightness and Quran Glass outside Follow my voice.

## Adding it to the glasses

Battery is added to the glasses from the Tidal Glass phone app, with the pairing in its address. The phone build
installed 2026-10-26 00:04 has a "Put Battery on my glasses" button, so the unlinked screen names it, on one line: "On
your phone, tap “Put Battery on my glasses” in Tidal Glass." (The earlier build added it through an "Add Battery to
your glasses" notification that the coordinator sends, and the hint then named no button.)

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
retries), which failed on 3b127b9. With that recheck, D2 and the first since-reconnect check changed on purpose (no "At
this pace" just after a change; today's rate before the pace since unplugging), and so did D11 with the unlinked
hint; each check says why.

The third rechecks' checks (section 3: Director A3, L, C, E, F and G; Photographer S1-S3 and the dot) fail on 3af1600,
and so do the checks they changed on purpose, each saying why: D11 (the hint names the button again), 4, D5 and N11
(the rows "Follow my voice" and "Tidal playing"), D7 (the list's message), D10 (charging: a time for 80%), the cache
check (v4), and R2e: 6 minutes after the switch, its 3 changes span 4 minutes, so the line now falls back and says so;
its time check moved to R2e8, 8 minutes in, and R2h6 moved to R2h8 for the same reason. Check 8 changed on purpose too
(calm dims the list's names and rates, not the whole list) and passes on both. The time line was also judged on real
readings with the Director's minute-by-minute replay, which stays outside this repository with its private data.
Captures are drawn with greyscale text (`--disable-lcd-text`), as on the glasses.
