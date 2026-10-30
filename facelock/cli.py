"""facelock: enrol, test and manage face unlock.

  sudo facelock enroll [user]   capture your face (replaces an old enrolment)
  sudo facelock improve [user]  add more of your face (dim light, glasses) without starting over
  sudo facelock calibrate [user]  redo the blink check calibration
  sudo facelock test [user]     run one unlock scan and show the scores
  sudo facelock remove [user]   delete your face data
  sudo facelock add-other [user]   capture someone who must NOT unlock (a look-alike relative)
  sudo facelock clear-others [user]  forget those faces
  facelock status               cameras, enrolled users and the service
  sudo facelock stats           unlocks, failures and timings, by day (last 14 days)
  sudo facelock report          a plain-text report to send to the developer
  sudo facelock update          install the newest release (--check: only look)
"""
import argparse
import os
import subprocess
import sys

from . import camera, config, scan, store


def default_user():
    return os.environ.get("SUDO_USER") or os.environ.get("USER") or ""


def need_root():
    if os.geteuid() != 0 and "FACELOCK_STATE" not in os.environ:
        sys.exit("Run this with sudo: face data is kept where only root can read it.")


def cmd_enroll(a):
    need_root()
    from .engine import Engine
    from .enroll import capture
    cfg = config.load()
    print(f"Enrolling {a.user}. Sit about an arm's length from the screen, in normal light.")
    try:
        vectors, strong, meta = capture(Engine(), cfg)
    except RuntimeError as e:
        sys.exit(f"Enrolment failed: {e}")
    store.save_enrolment(a.user, vectors, meta, strong=strong)
    print(f"Face saved: kept {meta['kept']} of {meta['faces']} face frames "
          f"(face ~{meta['median_face_px']:.0f}px, consistency {meta['self_similarity']}).")
    print(f"Your AuraFace bar: {meta['strong_accept']} (your face matches itself at "
          f"{meta['strong_self_p10']} or better 90% of the time; strangers stay below 0.35, "
          f"a look-alike brother reached 0.55).")
    if cfg.liveness != "off":
        print()
        do_calibrate(a.user, Engine(), cfg)
    print("Try it with: sudo facelock test")


def do_calibrate(user, engine, cfg):
    from .enroll import calibrate_blink
    print("Blink check calibration.")
    cal, info = calibrate_blink(engine, cfg)
    if cal is None:
        store.update_meta(user, blink=None)
        print(f"Couldn't tell your blinks apart on this camera (open {info['open_floor']}, "
              f"blinks {info['blink_dips']}). Try again with: sudo facelock calibrate")
        print("Until then the default blink setting is used.")
    else:
        drop, rise = cal
        store.update_meta(user, blink={"drop": drop, "rise": rise})
        print(f"Blink check calibrated (contrast below {drop} and {rise} lighter; "
              f"open eyes {info['open_floor']}, blinks {info['blink_dips']}).")


IMPROVE_MIN = 0.50  # AuraFace; strangers stay below 0.35


def cmd_improve(a):
    """Add more of your face to the enrolment (other light, glasses, angles)
    without starting over: learned samples and the adaptive bar are kept."""
    need_root()
    from .engine import Engine, top_similarity
    from .enroll import capture
    import numpy as np
    g = store.Gallery(a.user)
    if not g.strong_enrolled:
        sys.exit(f"Enrol first: sudo facelock enroll {a.user}")
    cfg = config.load()
    print(f"Adding to {a.user}'s face. Use the conditions face unlock struggles with: "
          "dim light, glasses, a different place to sit.")
    try:
        vectors, strong, meta = capture(Engine(), cfg)
    except RuntimeError as e:
        sys.exit(f"Capture failed: {e}")
    # only the enrolled person may be added: the new frames must clearly
    # match the existing enrolment with the strong model
    match = float(np.median([top_similarity(v, g.enrolled_strong) for v in strong]))
    from . import scan
    bar = scan.strong_bar(g, cfg)
    if match < IMPROVE_MIN:
        sys.exit(f"Not added: these frames match {a.user}'s enrolment at only {match:.2f} "
                 f"(strangers stay below 0.35). Only the enrolled person can be added. If the light "
                 f"was very different, try somewhere in between first, or enrol again.")
    if match < bar:
        # glasses or dim light lower the score: exactly what this is for,
        # so ask rather than refuse
        print(f"These frames match your enrolment at {match:.2f} (its strictest bar is {bar}), as "
              f"expected for glasses or dim light. Only add them if it was you alone in front of "
              f"the camera.")
        if not sys.stdin.isatty() or input("Add them? [y/N] ").strip().lower() not in ("y", "yes"):
            sys.exit("Not added.")
    count, group = store.add_enrolment(a.user, vectors, strong, p10=meta.get("strong_self_p10"))
    bars = scan.strong_bars(store.Gallery(a.user), cfg)
    print(f"Added as group {group}: {count} enrolled samples now (they matched your enrolment at "
          f"{match:.2f}). Its bar is {bars.get(group)}, from how closely these frames match "
          f"each other; in these conditions your face is judged against this group. "
          f"Other groups keep their bars.")


def cmd_calibrate(a):
    need_root()
    from .engine import Engine
    if not store.Gallery(a.user).enrolled_any:
        sys.exit(f"No face enrolled for {a.user}. Run: sudo facelock enroll")
    do_calibrate(a.user, Engine(), config.load())


def cmd_test(a):
    need_root()
    from .engine import Engine
    cfg = config.load()
    if a.no_blink:
        cfg.liveness = "off"
    gallery = store.Gallery(a.user)
    if not gallery.enrolled_any:
        sys.exit(f"No face enrolled for {a.user}. Run: sudo facelock enroll")
    if not gallery.strong_enrolled:
        sys.exit(f"{a.user}'s face was enrolled before the two-model check. Enrol again: sudo facelock enroll")
    print(f"{len(gallery.enrolled)} enrolled + {len(gallery.learned)} learned samples, "
          f"{len(gallery.others)} 'other' samples. Unlocks when {cfg.strong_frames} frames in a row show "
          f"one person with AuraFace at or above the bar of the sample group it matches "
          f"({', '.join(f'{k}: {v}' for k, v in scan.strong_bars(gallery, cfg).items())}) and SFace median >= "
          f"{cfg.accept}. Look at the camera" + (" and blink." if cfg.liveness == "blink" else "."))

    def verbose(t, score, strong, face, lum, blinked):
        if a.verbose:
            print(f"  {t:5.2f}s SFace {score:.3f} AuraFace "
                  + ("  -  " if strong is None else f"{strong:.3f}")
                  + f" face {face.size:.0f}px luma {lum:5.1f}" + (" blink" if blinked else ""))

    trace = [] if a.trace else None
    res = scan.run(Engine(), cfg, gallery, emit=lambda s: print(f"  [{s}]"), verbose=verbose, trace=trace)
    if a.trace:
        import json
        with open(a.trace, "w") as f:
            json.dump({"blink": gallery.blink, "frames": trace}, f)
        owner = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
        if all(owner):
            os.chown(a.trace, int(owner[0]), int(owner[1]))
        print(f"Trace (numbers only, no images) saved to {a.trace}")
    print(f"Result: {'UNLOCK' if res.ok else 'no unlock'} ({res.reason}); "
          f"AuraFace median {res.median_strong}, best {res.best_strong:.3f}; SFace best {res.best:.3f}; "
          f"first face at {res.first_face_s and round(res.first_face_s, 2)}s, took {res.elapsed}s, "
          f"brightness {res.luma}, failed attempts {res.failures}" + (", refused an 'other' face" if res.other_seen else ""))
    if res.strong_scores:
        print("AuraFace:", " ".join(f"{s:.2f}" for s in res.strong_scores[:40]))
    if res.scores:
        print("SFace:   ", " ".join(f"{s:.2f}" for s in res.scores[:40]))
    sys.exit(0 if res.ok else 1)


def cmd_add_other(a):
    """Capture a person who must never unlock as this user (a sibling, a
    look-alike). Any face closer to them than to you is refused."""
    need_root()
    from .engine import Engine
    from .enroll import capture
    g = store.Gallery(a.user)
    if not g.enrolled_any:
        sys.exit(f"Enrol {a.user} first: sudo facelock enroll")
    print(f"Capturing someone who must NOT unlock as {a.user}.")
    print(f"Ask them to sit in front of the camera ({a.user} should stay out of view).")
    try:
        vectors, strong, meta = capture(Engine(), config.load())
    except RuntimeError as e:
        sys.exit(f"Capture failed: {e}")
    from .engine import top_similarity
    closest = max(top_similarity(v, g.enrolled_strong) for v in strong) if g.strong_enrolled else 0.0
    if closest >= 0.70:
        sys.exit(f"That looks like {a.user} (similarity {closest:.2f}), so it wasn't saved. "
                 f"Make sure only the other person is in view.")
    g.add_others(vectors, strong)
    print(f"Saved {len(vectors)} samples. Their closest match to {a.user} was {closest:.2f}; "
          f"faces closer to them than to {a.user} are now refused.")
    print(f"Have them try: lock the screen and look at the camera. ({len(g.others)} 'other' samples in total)")


def cmd_clear_others(a):
    need_root()
    store.Gallery(a.user).clear_others()
    print("Forgotten.")


def cmd_remove(a):
    need_root()
    print("Removed." if store.remove(a.user) else f"No face data for {a.user}.")


def cmd_stats(a):
    """Summary of the scans in the history (about the last two weeks)."""
    need_root()
    from . import history
    events = history.read(days=a.days)
    scans = [e for e in events if e["kind"] == "scan"]
    if not events:
        sys.exit("No face unlock scans recorded yet. Use the lock screen for a while, then look again.")
    print(f"Face unlock, last {a.days} days ({len(scans)} scans)")
    print_summary(events)
    cfg = config.load()
    for user in store.users():
        g = store.Gallery(user)
        print(f"  {user}: AuraFace bar {bar_story(g, cfg)}, {len(g.learned)} learned samples")
    print("\nBy day:")
    days = {}
    for e in scans:
        d = days.setdefault(e["at"][:10], [0, 0, 0])
        d[0] += 1
        d[1] += e.get("result") == "matched"
        d[2] += e.get("failed", 0)
    for day, (n, ok, failed) in sorted(days.items()):
        print(f"  {day}  {n:3} scans  {ok:3} unlocked  {failed:3} failed tries")
    print(f"\nLast {min(a.last, len(events))} events:")
    for e in events[-a.last:]:
        print("  " + history.describe(e))


def print_summary(events):
    from . import history
    scans = [e for e in events if e["kind"] == "scan"]
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None  # noqa: E731
    counts = {}
    for e in scans:
        counts[e.get("result")] = counts.get(e.get("result"), 0) + 1
    for key, label in history.OUTCOME.items():
        if counts.get(key):
            print(f"  {label:24} {counts.pop(key)}")
    for key, n in counts.items():
        print(f"  {key or '?':24} {n}")
    ok = [e for e in scans if e.get("result") == "matched"]
    print(f"  failed tries (a face that didn't match): {sum(e.get('failed', 0) for e in scans)}")
    if ok:
        times = [e.get("seconds", 0) for e in ok]
        scores = [e.get("best", 0) for e in ok]
        print(f"  unlock time from camera on: median {med(times):.2f}s, slowest {max(times):.2f}s")
        print(f"  match score on unlocks: median {med(scores):.2f}, lowest {min(scores):.2f}")
        print(f"  dark (screen lit up): {sum(bool(e.get('dark')) for e in ok)}, "
              f"blink seen: {sum(bool(e.get('blink')) for e in ok)}, learned from: {sum(bool(e.get('learned')) for e in ok)}")
    refused = sum(bool(e.get("other")) for e in scans)
    if refused:
        print(f"  refused because the face was closer to an 'other' person: {refused}")
    old = sum(e['kind'] == 'relearn' for e in events)
    if old:
        print(f"  near misses learned by facelock 1.0.x (that data has been discarded): {old}")
    errors = [e for e in events if e["kind"] == "error" or e.get("camera_error")]
    print(f"  errors: {len(errors)}")


def cmd_report(a):
    """A plain-text report to send to the developer: no images, no face data."""
    need_root()
    import platform
    import time
    from . import __version__, history, update

    def run(*cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    def os_name():
        try:
            for line in open("/etc/os-release"):
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return platform.system()

    def cpu():
        try:
            for line in open("/proc/cpuinfo"):
                if line.startswith(("model name", "Hardware", "Model")):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
        return platform.machine()

    cfg_text = ""
    try:
        cfg_text = "\n".join(l for l in open(config.CONFIG_PATH).read().splitlines()
                             if l.strip() and not l.lstrip().startswith("#"))
    except OSError:
        cfg_text = "(no config file: defaults)"
    events = history.read(days=a.days)
    lines = [
        f"facelock report, {time.strftime('%Y-%m-%d %H:%M')}",
        "Contains versions, hardware, settings and scan results (times, scores, timings).",
        "No camera images, no face data and no passwords are included.",
        "",
        "== System",
        f"facelock {__version__}" + (f" (update available: {update.available()})" if update.available() else ""),
        f"OS: {os_name()}, kernel {platform.release()}, {platform.machine()}",
        f"CPU: {cpu()}",
        run("gnome-shell", "--version") or "GNOME Shell: ?",
        (run("gdm3", "--version") or run("gdm", "--version") or "GDM: ?"),
        "Cameras found: " + (", ".join(f"{p} ({c})" for p, c in camera.candidates()) or "none"),
        "All video devices: " + ", ".join(
            f"{os.path.basename(d)}={open(os.path.join(d, 'name')).read().strip()}"
            for d in sorted(__import__("glob").glob("/sys/class/video4linux/video*"))[:12]),
        "",
        "== Setup",
        f"service facelockd: {run('systemctl', 'is-active', 'facelockd')}, "
        f"update timer: {run('systemctl', 'is-active', 'facelock-update.timer')}",
        f"/etc/pam.d/gdm-face: {'present' if os.path.exists('/etc/pam.d/gdm-face') else 'MISSING'}",
        "keyring unlock: " + ("facelock (on)" if os.path.exists("/usr/share/pam-configs/facelock-keyring")
                              else "fp-keyring" if run("sh", "-c", "find /usr/lib /usr/lib64 -name pam_fp_keyring.so 2>/dev/null")
                              else "off"),
        "extension: " + ("installed" if os.path.isdir("/usr/share/gnome-shell/extensions/facelock@kumarsujay03.github.io")
                         else "MISSING"),
    ]
    for user in store.users():
        g = store.Gallery(user)
        lines.append(f"enrolled: {user} ({len(g.enrolled)} samples, {len(g.learned)} learned, "
                     f"enrolled {g.meta.get('enrolled_at', '?')})")
    lines += ["", "== Settings", cfg_text, "", f"== Last {a.days} days"]
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_summary(events)
    lines += [buf.getvalue().rstrip(), "", "== Every event"]
    lines += [history.describe(e) for e in events] or ["(none yet)"]
    lines += ["", "== Service log (last 60 lines)",
              run("journalctl", "-u", "facelockd", "-b", "--no-pager", "-n", "60", "-o", "short-iso"),
              "", "== Lock screen log (face unlock lines, this boot)",
              "\n".join(l for l in run("journalctl", "-b", "--no-pager", "-o", "short-iso").splitlines()
                        if "facelock" in l or "pam_face" in l or "gdm-face" in l)[-12000:]]
    owner = os.environ.get("SUDO_USER")
    home = os.path.expanduser(f"~{owner}") if owner else os.getcwd()
    path = a.output or os.path.join(home, time.strftime("facelock-report-%Y%m%d-%H%M.txt"))
    # a fresh file: an old one may belong to someone else (and /tmp won't let
    # root write into another user's file)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid and gid:
        os.chown(path, int(uid), int(gid))
    print(f"Report saved to {path}")
    print("Have a look, then send it to the developer (or attach it to a GitHub issue).")


def cmd_update(a):
    from . import __version__, update
    try:
        version, tar_url, sum_url, page = update.latest()
    except Exception as e:  # noqa: BLE001 - offline, rate limit, no release yet
        if a.timer:
            return
        sys.exit(f"Couldn't check for updates: {e}")
    if not update.newer_than_installed(version):
        update.remember(None)
        if not a.timer:
            print(f"facelock {__version__} is the latest version.")
        return
    if a.check or (a.timer and not config.load().auto_update):
        if os.geteuid() == 0:
            update.remember(version)
        print(f"facelock {version} is available (installed: {__version__}). {page}")
        if not a.timer:
            print("Install it with: sudo facelock update")
        return
    need_root()
    try:
        update.install(version, tar_url, sum_url)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Update failed: {e}")


def cmd_import_log(a):
    """Used by install.sh once: scans from before the history existed."""
    need_root()
    from . import history
    n = history.import_journal()
    if n:
        print(f"   kept {n} earlier scans from the log in the history")


def bar_story(g, cfg):
    """Each sample group's AuraFace bar and where it comes from."""
    from . import scan
    parts = []
    for gid, (rows, info) in scan.groups_of(g).items():
        bar = scan.group_bar(info, cfg)
        count = int(info.get("unlock_count", 0))
        how = ("enrolment" if gid == 0 else f"improve #{gid}")
        detail = (f"collecting unlocks {count}/{scan.ADAPT_AFTER}" if count < scan.ADAPT_AFTER
                  else f"raised by {count} unlocks")
        parts.append(f"group {gid} ({how}, {len(rows)} samples): {bar}, {detail}")
    return f"floor {scan.STRONG_FLOOR}; " + "; ".join(parts)


def cmd_status(a):
    from . import __version__, update
    print(f"facelock {__version__}" + (f" (update available: {update.available()}; run: sudo facelock update)"
                                       if update.available() else ""))
    print("Cameras:", ", ".join(f"{p} ({c})" for p, c in camera.candidates()) or "none found")
    cfg = config.load()
    print(f"Config: {config.CONFIG_PATH} (accept {cfg.accept} x{cfg.accept_frames}, "
          f"liveness {cfg.liveness}, "
          f"max failures {cfg.max_failures}, auto-update {'on' if cfg.auto_update else 'off'})")
    if os.access(config.STATE_DIR, os.R_OK):
        for user in store.users():
            g = store.Gallery(user)
            print(f"Enrolled: {user} ({len(g.enrolled)} samples, {len(g.learned)} learned, "
                  f"{len(g.others)} 'other' samples, blink calibration {g.blink or 'default'})")
            print(f"  AuraFace bar {bar_story(g, cfg)}")
    else:
        print("Enrolled users: (run with sudo to see)")
    state = subprocess.run(["systemctl", "is-active", "facelockd"], capture_output=True, text=True).stdout.strip()
    print("Service facelockd:", state or "unknown")


def main():
    ap = argparse.ArgumentParser(prog="facelock", description="Face unlock for the login and lock screens.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("enroll", cmd_enroll), ("improve", cmd_improve), ("calibrate", cmd_calibrate), ("test", cmd_test),
                     ("remove", cmd_remove), ("add-other", cmd_add_other), ("clear-others", cmd_clear_others)):
        p = sub.add_parser(name)
        p.add_argument("user", nargs="?", default=default_user())
        p.set_defaults(fn=fn)
        if name == "test":
            p.add_argument("-v", "--verbose", action="store_true", help="print every frame")
            p.add_argument("--no-blink", action="store_true", help="skip the blink check")
            p.add_argument("--trace", metavar="FILE", help="save per-frame blink-check numbers to FILE")
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("stats", help="unlocks, failures and timings, by day")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--last", type=int, default=15, help="list this many recent events")
    p.set_defaults(fn=cmd_stats)
    p = sub.add_parser("report", help="save a plain-text report to send to the developer")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("-o", "--output", help="where to save it (default: your home folder)")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("import-log", help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_import_log)
    p = sub.add_parser("update", help="install the newest release from GitHub")
    p.add_argument("--check", action="store_true", help="only say whether there is one")
    p.add_argument("--timer", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_update)
    a = ap.parse_args()
    if hasattr(a, "user") and not store.valid_user(a.user):
        sys.exit("Give a valid user name.")
    a.fn(a)


if __name__ == "__main__":
    main()
