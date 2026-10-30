# facelock

[![CI](https://github.com/Kumarsujay03/facelock/actions/workflows/ci.yml/badge.svg)](https://github.com/Kumarsujay03/facelock/actions/workflows/ci.yml)

Face unlock for the GNOME login and lock screens on Linux, like a phone or
Windows Hello: wake the screen, look at the camera, it unlocks. Fingerprint
and password keep working at the same time; whichever succeeds first wins.

- Works with the laptop's ordinary built-in webcam. Intel, AMD and ARM64 all
  work: everything runs on the CPU and no GPU or NPU is needed.
- Used **only** by the login screen and the lock screen. sudo, polkit and
  apps never use it (the PAM module refuses to run in any other PAM service).
- The camera turns on only when you wake the screen, for at most a few seconds.
- It learns how you look in different light and from different angles as you
  use it.
- In a dark room the screen briefly turns white to light your face.
- After 3 failed tries it steps aside: use your fingerprint or password.
- Optionally it unlocks your login keyring too (saved Wi-Fi and browser
  passwords), so a face login is a complete login.

> **Status: 1.0, tested on Ubuntu 26.04 with GNOME 50.** Other distributions
> and GNOME 46–49 should work but are untested. Reports are very welcome; see
> [Problems and reports](#problems-and-reports).

## Requirements

- GNOME Shell with GDM (the GNOME login screen), on a systemd distribution:
  Ubuntu, Debian, Fedora, Arch, openSUSE...
- A built-in or USB camera that shows up as `/dev/video*`. Most AMD and Intel
  laptops have a USB (UVC) webcam. Newer Intel laptops with an IPU6/IPU7 MIPI
  camera need the Intel camera relay (`v4l2-relayd`) that Ubuntu installs for
  them. On Snapdragon X laptops the camera must already work in Linux.
- Internet once during installation (about 300 MB). It downloads the three face models and
  installs NumPy and OpenCV into a private Python environment in
  `/opt/facelock`; your system's Python is not touched.

## Install

Download the latest release, or clone the repository, then:

```sh
tar xf facelock-1.3.0.tar.gz && cd facelock-1.3.0     # or: git clone https://github.com/Kumarsujay03/facelock && cd facelock
sudo ./install.sh
```

The installer installs the build tools it needs from your distribution, builds
the PAM modules and sets everything up. It asks one question: whether to
unlock the keyring after a face login (see [Keyring](#keyring)).

Then:

```sh
sudo facelock enroll     # look at the camera, follow the head turns, then blink 3 times
sudo facelock test       # one scan: shows the match score and how long it took
```

Log out and back in once so GNOME loads the extension. Then lock the screen
(Super+L), wait two seconds, move the mouse and look at the camera.

## Use

| Situation | What happens |
|---|---|
| Screen locked and dark, untouched | Camera off. Nothing runs. |
| A key, the mouse or the touchpad after a moment of quiet, opening the lid | One scan: about 1–1.5 seconds when it recognises you (5 frames, two models). |
| Just locked with Super+L | No scan for the first 2 seconds. |
| Login screen after boot or logout | Scans as soon as your user is shown. |
| Dark room | After a first try in the room's light, the screen fades to white at full brightness for a moment, then back. Once until the screen has been off. |
| Esc during a scan | Stops it: camera off, back to fingerprint and password. |
| Not recognised | The face icon shakes: "Try again". |
| 3 failed tries | "Use fingerprint or password". Face unlock waits until you have unlocked another way. |
| Someone added with `facelock add-other` | Refused, every time. |
| No face in view | Nothing counts as a failure; the next wake scans again. |

On the lock screen, a small icon at the top shows what's happening: a face
while the camera looks, a shake for a miss, a wink when it unlocks.

## Commands

```
sudo facelock enroll      capture your face (replaces an old enrolment)
sudo facelock test        one scan with the scores; -v shows every frame
sudo facelock stats       how face unlock is doing: unlocks, failures, timings, by day
sudo facelock report      a plain-text report to send if something is wrong
sudo facelock update      install the newest release (--check only looks)
facelock status           version, camera, service, enrolled users
sudo facelock improve     add more of your face (dim light, glasses) without starting over
sudo facelock add-other   capture someone who must NOT unlock (a look-alike relative)
sudo facelock clear-others   forget those faces
sudo facelock calibrate   redo the blink calibration
sudo facelock remove      delete your face data
```

Settings live in `/etc/facelock/facelock.conf` (every option is explained
there) and apply from the next scan.

## How it learns

- **Only from strong unlocks:** a frame is learned only if it is very close
  to your *enrolled* face with both models (AuraFace 0.75+, SFace 0.68+; a
  look-alike brother reached 0.55 and 0.57). It saves the frame that differs
  most from what it already knows (a new angle, new light, glasses), keeps up
  to 40, and replaces the most redundant one.
- **A bar per condition.** Your enrolment and every `facelock improve`
  are separate sample groups, each with its own bar measured from that
  capture: how closely those frames matched each other, minus 0.08, never
  below 0.62. Your face is judged against the group it matches best, so good
  light has a strict bar and dim light its own (lower) one, instead of one
  bar from your best conditions refusing you in the others.
- **Each bar rises with you, slowly.** Every face unlock records how well
  you matched the group it used. After 20 unlocks of a group, while your
  recent median there is 0.70 or more, each unlock raises that group's bar
  by at most 0.005 towards your recent median minus 0.12 (and below your
  typical worst unlock), up to 0.75. Good-light unlocks never tighten the
  dim-light bar. Bars never go down; enrolling again resets them.
  Enrolments from before 1.3 are split into groups automatically.
  `facelock status` shows every group's bar.
- **Teach it on purpose:** `sudo facelock improve` captures your face again
  and adds it as a new group with its own bar, keeping everything learned.
  Run it in conditions where face unlock struggles (dim light, glasses,
  another room). Only the enrolled person can be added: frames matching
  your enrolment at your bar are added; between 0.50 and your bar (normal
  with glasses or in dim light) it shows the score and asks first; below
  0.50 it refuses (strangers stay below 0.35). Up to 48 enrolled samples are
  kept, the most varied ones.
- Learned samples only help a face that is already close to your enrolment,
  and your enrolment samples never change.
- facelock 1.0.x also learned from "near misses" and from weaker unlocks.
  That let a look-alike relative's face be learned, so it was removed in
  1.1.0, and data learned by 1.0.x is deleted when you update.

## Blink check (liveness): still being tested

An ordinary colour camera can be fooled by a good photo of you. Asking for a
blink defends against that, but it isn't reliable enough on every camera yet.
So by default (`liveness = observe`) the blink check only **watches and
records** what it measures on each scan; it never blocks an unlock. After a
week or two, `sudo facelock stats` and `sudo facelock report` show how it did.
Shared reports from different laptops will decide when it becomes the default.
To require a blink now, set `liveness = blink`.

Laptops with an infrared (Windows Hello) camera could do much better; IR
cameras aren't used yet.

## Keyring

A face login can't type your password, so on its own the GNOME login keyring
stays locked and asks for your password once.

If you answer yes during installation, the password you type at the login or
lock screen is sealed on this machine and unsealed only after your face
matches:

- **With a TPM:** bound to the TPM and Secure Boot state.
- **Without a TPM:** bound to the systemd host key.

The sealed copy is updated whenever you change your password and type the new
one. Without full-disk encryption, someone who can take the disk out and
change the OS could get at it, so turn it off if that matters to you:
`sudo ./install.sh --no-keyring` on a fresh install, or `sudo ./uninstall.sh`
and install again.

This needs `pam-auth-update` (Debian, Ubuntu and their derivatives). On other
distributions the keyring asks for your password once after a face login.

## Updates

The installation checks GitHub once a day for a new release (the
`facelock-update.timer`). `facelock status` mentions one when there is.
Install it with:

```sh
sudo facelock update
```

It downloads the release over HTTPS, checks its SHA-256 checksum and runs its
installer, keeping your settings, face data and keyring choice. To install
updates automatically, set `auto = true` under `[update]` in the config. Log
out and back in afterwards to load a new lock screen extension.

## Problems and reports

Everything face unlock does is kept for about two weeks:

- `sudo facelock stats` gives a readable summary.
- `sudo facelock report` writes one plain-text file (versions, hardware,
  camera, settings, scan results, errors). **No camera images, no face data
  and no passwords** are in it; read it before sending.

Attach it to a [GitHub issue](https://github.com/Kumarsujay03/facelock/issues).

Live logs: `journalctl -u facelockd -f`.

## Troubleshooting

**`git clone` or the installer fails with "SSL certificate verification
failed" / "certificate signer not trusted".** The laptop doesn't trust the
certificate it receives for GitHub. The installer downloads the face models
and Python packages over HTTPS too, so fix this first:

1. Check the date and time (`date`); a wrong clock breaks every certificate.
2. Update the trusted certificates:
   `sudo apt update && sudo apt install --reinstall ca-certificates && sudo update-ca-certificates`
   (Fedora: `sudo dnf reinstall ca-certificates`; Arch: `sudo pacman -S ca-certificates`).
3. See who signed what you received:
   `echo | openssl s_client -connect github.com:443 -servername github.com 2>/dev/null | grep 'i:'`
   It should name Sectigo / USERTrust. A VPN, company proxy or antivirus name
   instead (Cloudflare WARP, Zscaler, Kaspersky, Fortinet...), or the error
   "self-signed certificate in certificate chain", means something intercepts
   HTTPS. Either turn it off while installing, or make the system trust it:
   save its root certificate (the last one printed by
   `echo | openssl s_client -showcerts -connect github.com:443 -servername github.com`,
   from `-----BEGIN CERTIFICATE-----` to `-----END CERTIFICATE-----`) as
   `/usr/local/share/ca-certificates/proxy.crt`, then run
   `sudo update-ca-certificates`. Only do that for a proxy you know and trust.

   `git clone` can work while the installer fails, because the installer runs
   as root with the system's certificates, while your own shell may carry
   extra ones (for example from Conda or a company setup).

**The camera isn't found or "Camera unavailable".** Close apps using the
camera (browsers, video calls); `facelock status` lists the cameras it sees.

## Uninstall

```sh
sudo ./uninstall.sh              # also deletes your face data
sudo ./uninstall.sh --keep-data
```

## How it works

```
wake the screen ──> GNOME Shell extension ──starts──> GDM conversation "gdm-face"
                         ▲                                   │
     "facelock:*" info   │                                   ▼
     messages (icon,     │                   /etc/pam.d/gdm-face: pam_face.so
     white light, ...)   │                                   │ /run/facelock/socket
                         └───────────── facelockd (root) <───┘
                                         camera → YuNet face detection
                                                → SFace (fast first check)
                                                → AuraFace (deciding check)
                                                → blink check
                                                → learning
```

- **facelockd** (`/opt/facelock`): owns the camera and the face data. It runs
  as a sandboxed systemd service with no network and no access to home
  folders. The face data in `/var/lib/facelock/<user>/` is readable by root
  only.
- **pam_face.so**: asks facelockd for a scan and passes on its progress.
- **/etc/pam.d/gdm-face**: your system's `gdm-password` with the password
  check replaced by `pam_face`, followed by the optional keyring unlock.
- **The extension** (`facelock@kumarsujay03.github.io`): starts the face
  service when you wake the screen, shows the icon, lights your face in the
  dark, and keeps a failed face scan from ever disturbing the password prompt.

Camera choice: a relayed camera (`v4l2loopback`, as with Intel IPU6/7), else
a USB webcam (`uvcvideo`), else any other V4L2 capture device. Raw ISP nodes
and IR cameras are skipped. Set `device = /dev/videoN` in the config to choose
one yourself.

## Security notes

- **How a face is recognised.** Not by comparing pictures: each frame's
  face is aligned by its landmarks and described by two independent neural
  networks as a list of numbers capturing its features: the shape and layout
  of the eyes, nose, mouth and jaw.
  - **SFace:** 128 numbers, fast.
  - **AuraFace:** 512 numbers, a ResNet100 trained with ArcFace's angular
    margin loss, and much better at telling similar faces apart.

  It unlocks only when **5 frames in a row** show the same person, AuraFace's
  median similarity to your enrolment reaches your bar, no frame falls far
  below it, and SFace agrees.
- **Measured.** On the LFW benchmark, running the real decision code on
  14,280 owner/intruder pairs (120 people), no intruder unlocked. The
  highest any stranger's 5-photo median reached was 0.34. A real look-alike
  brother reached 0.55 frame by frame (his brother: 0.78–0.83), so nobody's
  bar is below 0.62, and enrolment sets a higher one for a face that matches
  itself closely. Settings can only raise these bars.
- **Look-alike relatives.** Family is the hard case for any webcam face
  unlock. In facelock 1.0.x a brother unlocked his sibling's laptop (single
  model, scores 0.48–0.54 against a bar of 0.45) and his face was then
  learned. 1.1 tightened the rules and stopped near-miss learning, and 1.2
  adds AuraFace and the 5-frame check. If someone in your home looks like
  you, also add them as "not me":

  ```sh
  sudo facelock add-other     # they sit in front of the camera, you stay out of view
  ```

  Any face closer to them than to you is then refused, and `facelock stats`
  counts those refusals. Then have them try the lock screen. If they still get
  in, raise `strong_accept` in `/etc/facelock/facelock.conf` and tell us
  (with `sudo facelock report`), or don't use face unlock on that laptop.

- Face data never leaves the machine. Update checks are the only network
  access, and they come from the update timer, not the face service.
- With a colour camera, a photo or video of you may be enough to unlock until
  the blink check is required; see above. Keep your password for anything
  that matters more than the lock screen. sudo never uses face unlock.
- After 3 failed tries, face unlock stays off until you unlock another way.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -I tests/test_offline.py          # unit tests, no camera needed
.venv/bin/python -I tests/record_blinks.py         # record labelled blink data (numbers only)
.venv/bin/python -I tests/replay_blinks.py         # replay it through the blink check
```

Every push runs CI: shellcheck, the extension's syntax, unit tests, the
model downloads, PAM module builds on Ubuntu and Fedora, and a full install
from the release archive on a fresh Ubuntu with GDM. That install checks the
service, the PAM module (including that it refuses anything but `gdm-face`),
the keyring option, updating in place, and a clean uninstall.

Releases: bump `VERSION`, add a `## <version>` entry to `CHANGELOG.md`,
commit and push, wait for CI, then run `tools/make-release.sh <version>`. It
pushes the tag; GitHub Actions runs every check again on that commit and
publishes the release (archive, checksum, notes from the changelog) only if
they all pass.

## Licence

MIT, see [LICENSE](LICENSE). The face models are downloaded at install:
YuNet (MIT) and SFace (Apache-2.0) from the
[OpenCV model zoo](https://github.com/opencv/opencv_zoo), and AuraFace
(Apache-2.0) from [fal/AuraFace-v1](https://huggingface.co/fal/AuraFace-v1).
See [models/README.md](models/README.md).
