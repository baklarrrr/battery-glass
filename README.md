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

## How the numbers are worked out

- **Tick to tick.** Levels move in whole percents, so a rate is measured from one change of the level to another,
  never from a single change.
- **Only readings the phone could vouch for.** A step between two readings is skipped when either was disconnected,
  had charging or wearing unknown, or when the level fell more than 2 points between them (a reconnect, or a phone
  that was off). This assumes the toolkit reports every whole percent, which is not yet checked on the real glasses.
- **How fast they drain** lists a situation only when the time between two changes stayed in that one situation, with
  at least 3 points over 30 minutes; rates are whole numbers. A situation with no fall at all over h hours says
  "under X% an hour" (X = 1/h, rounded up), never 0. The band's note follows the same rule, timed by when the band was
  read, so a band the phone has stopped reading adds no hours.
- **Situations:** Not worn; Follow my voice (the glasses' microphone on; Quran Glass's own commands come only then);
  Tidal Glass open; Sound Lab open; Music playing; Worn, anything else.
- **When to charge** is said as clock times ("20% around 9:30 PM · empty around 11:30 PM"), from the current stretch
  off the charger: it starts after the last reading that was charging, unknown or disconnected, and needs 3 changes
  of the level. Until then, today's rate for what is going on now is used, and the line says so ("if Tidal Glass stays
  open"). Times are rounded to 5 minutes within the hour, 15 within 3 hours, 30 beyond. While charging: "Full around
  …" from this charge's own readings, or nothing.
- **The chart** breaks where the readings stop being continuous (a jump of more than 2 points, or over 30 minutes of
  unknown or disconnected readings). The phone writes a line only on a change, so a long quiet stretch stays joined.

Not counted, because the phone cannot see them: Meta's own apps, the display's brightness, the radios, Quran Glass
outside Follow my voice, and Bakar Studio or Bakar Home being open (they talk to the PC, not the phone). One dim line
under the list names Meta's apps, brightness and Quran Glass outside Follow my voice.

## Privacy

The page code is public; nothing private is in it. The pairing (topic and AES-GCM key) reaches the glasses only in
the app's address fragment, which never goes to a server, and every message is sealed with that key, so the public
MQTT brokers (EMQX, HiveMQ, shiftr.io, all opened together) carry only ciphertext. The last answer kept on the glasses
stays in this app's own storage there.

## Checks

`python -m http.server 47641 --bind 127.0.0.1 --directory webapp`, then
`python webapp/tests/battery_test.py [base_url] [shots_dir]`. The test fakes the brokers and the glasses' own battery,
blocks service workers (a controlled page would load the real MQTT library), and plays the phone, so no message
leaves the PC. It includes the Director's harness cases (B01-B05, T1-T3), which fail on 78e18cc.
