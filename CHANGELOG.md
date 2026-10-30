# Changelog

## 1.0.4

- The installer checks that HTTPS downloads work before changing anything,
  and explains certificate errors (an intercepting VPN, proxy or antivirus)
  instead of stopping halfway with curl's message.
- README: how to trust a known HTTPS-intercepting proxy.

## 1.0.3

- Esc during a face scan stops it: the camera turns off at once and the
  icon goes back to the fingerprint. The next wake scans again.
- The white light is now a second try: face unlock first looks in the
  room's own light for 1.5 seconds (`light_after`), and the screen lights up
  at most once until it has been off again.
- Fixed: when the white light ended by itself, the lock screen icon could
  stay in dark ink.

## 1.0.2

- Fixed: if the lock screen restarted its sign-in check during a face scan
  (for example right as the screen lit up in the dark), the white light stayed
  on and no new scan started until another key press. The light now goes out
  and the scan starts again on its own.
- Fixed: each wake could fire several times as input watches piled up.
- The white light now stays at most 6 seconds.

## 1.0.1

- Updating from a build that had no scan history now copies the earlier
  scans from the system log into it, so `facelock stats` and `facelock
  report` include them.

## 1.0.0

First public release.

- Face unlock for the GDM login and lock screens, next to fingerprint and password.
- Scans only when you wake the screen; about half a second to unlock.
- Learns from confident unlocks and from near misses you then confirm with
  your fingerprint or password.
- Low light: the screen fades to white at full brightness for a moment.
- 3 failed tries, then it waits until you unlock another way.
- Optional keyring unlock after a face login (TPM-sealed password).
- Blink check in observe mode: measured and recorded, not yet required.
- `facelock stats` and `facelock report` (two weeks of scan history, no face data).
- `facelock update` and a daily update check.
