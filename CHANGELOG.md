# Changelog

## 1.2.0

**Security: two face models must agree, over 5 frames.**

- A second, much stronger face model, AuraFace (512 features per face,
  ResNet100 trained with ArcFace's angular margin loss, Apache-2.0), decides;
  SFace stays as the first check.
- It unlocks only when 5 frames in a row show one person, AuraFace's median
  reaches the user's bar (0.55 at least, higher if enrolment measured a very
  consistent face), no frame is far below it, and SFace agrees.
- Measured with the real decision code on the LFW benchmark: no intruder
  unlocked in 14,280 owner/intruder pairs; the highest stranger median was
  0.34.
- Unlocking takes about 1-1.5 s instead of 0.5 s.
- **Enrol again after updating** (`sudo facelock enroll`): older enrolments
  have no AuraFace data, and face unlock refuses to work on SFace alone.
- `facelock add-other` captures both models too.
- The install downloads about 260 MB more (the AuraFace model).

## 1.1.0

**Security fix.**

A look-alike relative could unlock: a brother unlocked his sibling's laptop
three times, and his face was then learned as the owner's.

- Stricter matching: a face must score 0.58 on 3 frames, or 0.66 on one
  frame against the enrolled face (1.0.x: 0.45 on 2 frames).
- Learned samples only count for a face already close to the enrolment.
- Learning only from frames 0.68 or closer to the enrolled face.
- Near-miss learning removed: it learned whatever face was in view when the
  desktop was next unlocked, even if that unlock was by the look-alike.
- Data learned by 1.0.x is deleted on update.
- New `sudo facelock add-other` / `clear-others`: faces of people who must
  not unlock; a face closer to them than to you is refused.
- Old match settings in `/etc/facelock/facelock.conf` are replaced (a copy
  is kept as `facelock.conf.1.0`); they let look-alikes in.

## 1.0.5

- Fixed for fresh installs: on stock Ubuntu the login screen didn't read
  facelock's settings (its dconf profile lacks `system-db:gdm`), so face
  unlock could be missing from the login screen. The installer now sets this up.
- Fixed: installing failed when no GNOME extension had been installed
  system-wide before, or when `/etc/dconf/profile` didn't exist.
- `facelock report` can replace an older report written by someone else.
- Every change is now checked by GitHub Actions, including a full install,
  update and uninstall on a fresh Ubuntu with GDM, and releases are only
  published once all checks pass.

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
