# Changelog

## 1.3.2

- Someone else's failed tries no longer lock the owner out of face unlock
  for long: the first 3 failed tries pause face unlock for 30 seconds
  (`pause_seconds`); only if 3 more fail does it wait for a fingerprint or
  password unlock. (A brother's 3 tries paused it until a fingerprint
  unlock, and the owner's key presses then silently did nothing.)
- While paused, every key press shows why on the lock screen ("Face unlock
  paused for 25 s: use fingerprint or password") instead of doing nothing.

## 1.3.1

- New `sudo facelock keyring`: checks that a face login also unlocks the
  keyring (module, PAM files, sealed password, that it unseals), without
  showing the password.
- `facelock help` (or `facelock` alone) lists every command with what it does
  and examples; `facelock <command> --help` explains one, with an example.
- Fixed: `facelock status` crashed (it used a setting removed in 1.2.0).
- Tests now check that every setting the code uses exists, and CI runs
  `status`, `stats`, `keyring` and `update --check` after installing.
- Fixed before release: `facelock keyring` searched /usr/lib recursively,
  following symlinks, which could run for hours (it hung CI). It now looks
  in the PAM module folder only. CI gives every command 60 s and every job
  20 minutes.
- CI caches the face models between runs (the 260 MB download happens only
  when they change).

## 1.3.0

**A bar per condition.**

- Problem: one bar per user, set by the best capture. A friend enrolled in
  good light; `improve` in similar light raised his bar to 0.75; in dim
  bedroom light his own face scores 0.56-0.71, so he was refused every
  time, even after adding dim-light samples.
- Each capture (the enrolment, every `facelock improve`) is now a sample
  group with its own bar, measured from that capture (minus 0.08, never
  below the 0.62 floor). A face is judged against the group it matches best,
  with that group's bar. `improve` no longer raises a single global bar.
- The adaptive bar works per group: good-light unlocks never tighten the
  dim-light bar.
- Enrolments from 1.2.x are split into groups automatically on the first
  scan after updating; no new enrolment or improve is needed. Samples that
  agree with each other (good light, dim light, glasses) become a group;
  each group's bar comes from its own samples, keeping the old bar (at most
  0.70, since 1.2.7 could have raised it from one well-lit capture) where
  the samples clearly support it. On LFW faces: 0 of 900 stranger photos
  passed any group's bar; 114 of 118 of the owners' photos from other years
  passed.
- New tests for rules that must always hold: every condition a user
  enrolled unlocks, every bar is between 0.62 and 0.75, `improve` never
  changes other groups' bars, strangers never pass.
- `facelock status` and `stats` list every group and its bar.

## 1.2.7

- `facelock improve` raises the user's personal bar when the new capture
  shows their face is more consistent than measured at enrolment (never
  lowers it). Every bar is measured per user; only the 0.62 floor is shared.

## 1.2.6

**Security: wider margin after adding glasses frames.**

- Tested again with the look-alike brother after the owner re-enrolled and
  added frames with glasses (`facelock improve`, 32 samples): refused, but
  his best 5-frame AuraFace median came within 0.04 of the bar, and with
  glasses he passed SFace.
- A face's score is now the mean of its k closest samples with k growing
  with the enrolment (3 up to 23 samples, then one per 6), so a larger
  enrolment doesn't make lucky matches easier.
- Personal bar: enrolment consistency minus 0.08 (was 0.12), up to 0.75.
  For the tested owner: 0.675, while his own scans run at 0.74-0.84.
- The brother's real frames from both tests are unit tests: refused with at
  least 0.07 margin even with SFace taken out.
- `facelock add-other` is strongly recommended for look-alike relatives.

## 1.2.5

- Fixed: enrolling or `facelock improve` could fail with "the frames don't
  agree with each other" for one person, especially with glasses. The check
  compared samples with an unnormalised average, and a single odd frame
  (glare, a blink mid-turn) failed the whole capture. Odd frames are now
  dropped; it fails only if fewer than 8 frames agree on one face.
- `facelock improve` asks before adding frames that match the enrolment
  below the user's bar but at 0.50 or more (glasses, dim light: what it is
  for), and refuses below 0.50 (strangers stay below 0.35).

## 1.2.4

- New `sudo facelock improve`: add more of your face to the enrolment (dim
  light, glasses, another room) without starting over; learned samples and
  the adaptive bar are kept. The new frames must match the existing
  enrolment at the user's bar, so only the enrolled person can be added. Up
  to 48 enrolled samples, the most varied kept.

## 1.2.3

- The adaptive bar is gentler: it only collects scores for the first 20
  face unlocks, then rises by at most 0.005 per unlock while the user's
  recent median is 0.70 or more, towards that median minus 0.12 (below
  their typical worst unlock), capped at 0.72. It never goes down.

## 1.2.2

- The AuraFace bar now rises with the user: after 10 face unlocks it follows
  the user's typical worst unlock (10th percentile of the last 30) minus
  0.10, capped at 0.72, never below the enrolment bar or 0.62. As learning
  raises the user's own scores, the bar rises with them. Enrolling again
  resets it.
- `facelock status` and `facelock stats` show each user's bar and where it
  comes from.
- Tested on a real non-relative (the owner's mother): SFace never passed
  0.37, so AuraFace was never even needed.

## 1.2.1

**Security: wider margin against look-alikes.**

- Tested with a real look-alike brother on 1.2.0: refused, but some of his
  AuraFace frames reached 0.55, the 1.2.0 bar, while the owner scored
  0.78-0.83.
- AuraFace bar floor raised to 0.62, enforced in code: settings can raise
  the bars but no longer lower them below 0.62 (AuraFace) and 0.58 (SFace).
- Each user's bar now comes from how consistently their face matched
  itself at enrolment (minus 0.12), between 0.62 and 0.72. For the tested
  owner that is 0.64: 0.14 below his worst frame, 0.09 above his brother's
  best. No new enrolment needed.

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
