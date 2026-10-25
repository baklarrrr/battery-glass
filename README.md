# Battery

A glasses app for Meta Ray-Ban Display: the glasses' and the band's battery now, the time left at recent use, and
what uses the glasses, worked out from real readings.

## Where the numbers come from

The Tidal Glass app on the paired phone keeps the readings (`BatteryWatch.kt` in the phone app):

- the glasses' level, charging and worn state, from Meta's Wearables toolkit;
- the band's level, read over its standard Bluetooth battery service (the toolkit reports the glasses only);
- which of these glasses apps is in use (Tidal Glass, Sound Lab, Quran Glass) and Quran listening.

Every change appends one line, kept 7 days on the phone. This page asks for them over the phone's sealed link
(`batHistory` on `bkr/<topic>/c`, answered on `bkr/<topic>/bat`) when it opens and every 5 minutes while on screen.

Drain is counted only between two readings of the same device while it is not charging, and grouped by what was
going on. The levels move in whole percents, so a situation needs about ten minutes before it is listed.

Not counted, because the phone cannot see them: Meta's own apps, the display's brightness, the radios, and Bakar
Studio or Bakar Home being open (they talk to the PC, not the phone).

## Privacy

The page code is public; nothing private is in it. The pairing (topic and AES-GCM key) reaches the glasses only in
the app's address fragment, which never goes to a server, and every message is sealed with that key, so the public
MQTT brokers (EMQX, HiveMQ, shiftr.io, all opened together) carry only ciphertext.

## Checks

`python -m http.server 47641 --bind 127.0.0.1 --directory webapp`, then `python webapp/tests/battery_test.py` (fake
brokers; the test plays the phone; no message leaves the PC).
