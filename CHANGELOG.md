# Changelog

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
