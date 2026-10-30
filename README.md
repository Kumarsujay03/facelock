# facelock

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
- Internet once during installation. It downloads the two face models and
  installs NumPy and OpenCV into a private Python environment in
  `/opt/facelock`; your system's Python is not touched.

## Install

Download the latest release, or clone the repository, then:

```sh
tar xf facelock-1.0.3.tar.gz && cd facelock-1.0.3     # or: git clone https://github.com/Kumarsujay03/facelock && cd facelock
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
| A key, the mouse or the touchpad after a moment of quiet, opening the lid | One scan, about half a second when it recognises you. |
| Just locked with Super+L | No scan for the first 2 seconds. |
| Login screen after boot or logout | Scans as soon as your user is shown. |
| Dark room | After a first try in the room's light, the screen fades to white at full brightness for a moment, then back. Once until the screen has been off. |
| Esc during a scan | Stops it: camera off, back to fingerprint and password. |
| Not recognised | The face icon shakes: "Try again". |
| 3 failed tries | "Use fingerprint or password". Face unlock waits until you have unlocked another way. |
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
sudo facelock calibrate   redo the blink calibration
sudo facelock remove      delete your face data
```

Settings live in `/etc/facelock/facelock.conf` (every option is explained
there) and apply from the next scan.

## How it learns

- **After confident unlocks:** it saves the frame that differs most from what
  it already knows (a new angle, new light, glasses). It keeps up to 40 and
  replaces the most redundant one.
- **After near misses:** when a scan saw a face that was probably you (score
  ≥ 0.38; strangers score about 0.2) but didn't unlock, and you then unlock
  with your fingerprint or password within a minute, that frame is learned.
  This teaches it exactly the conditions it struggles with.
- Your enrolment samples never change, and every learned sample must also
  match them, so it can't drift towards someone else.

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
                                                → SFace recognition
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

Releases: `tools/make-release.sh <version>` builds `facelock-<version>.tar.gz`
and its checksum and publishes them as a GitHub release.

## Licence

MIT, see [LICENSE](LICENSE). The face models come from the
[OpenCV model zoo](https://github.com/opencv/opencv_zoo): YuNet (MIT) and
SFace (Apache-2.0). See [models/README.md](models/README.md).
