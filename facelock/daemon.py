"""facelockd: owns the camera and the face data; answers pam_face.

Protocol on the root-only Unix socket, one line per message:
  client: AUTH <user>
  daemon: MSG <text>              (pam_face shows it as PAM info; the lock
                                   screen extension reads "facelock:*" ones)
  daemon: OK | FAIL <why> | UNAVAIL <why>
"""
import logging
import os
import pwd
import select
import socket
import struct
import subprocess
import sys
import threading
import time

from . import camera, config, history, scan, store
from .engine import Engine, similarity

log = logging.getLogger("facelockd")


GALLERY_LOCK = threading.Lock()  # one writer of the face data at a time


class Lockouts:
    """Failed attempts per user. After max_failures, face unlock pauses until
    the user's desktop is seen unlocked (password or fingerprint worked).

    Also near misses: a scan that saw a face close to the user's but didn't
    unlock (dim light, an unusual angle) is learned once the user proves it
    was them by unlocking another way soon after. That teaches face unlock
    exactly the conditions it struggles with."""

    def __init__(self):
        self.failures = {}
        self.pending = {}   # user -> (embedding, monotonic time, score)
        self.lock = threading.Lock()
        threading.Thread(target=self._watch, daemon=True).start()

    def remember(self, user, vec, score):
        with self.lock:
            self.pending[user] = (vec, time.monotonic(), score)

    def get(self, user):
        with self.lock:
            return self.failures.get(user, 0)

    def add(self, user, n):
        with self.lock:
            self.failures[user] = self.failures.get(user, 0) + n
            return self.failures[user]

    def clear(self, user):
        with self.lock:
            self.failures.pop(user, None)

    def _watch(self):
        while True:
            time.sleep(3)
            cfg = config.load()
            with self.lock:
                now = time.monotonic()
                for user in [u for u, (_, t, _) in self.pending.items() if now - t > cfg.relearn_seconds]:
                    del self.pending[user]
                users = {u for u, n in self.failures.items() if n} | set(self.pending)
            for user in users:
                if not desktop_unlocked(user):
                    continue
                if self.get(user):
                    log.info("%s unlocked another way; face unlock allowed again", user)
                    self.clear(user)
                with self.lock:
                    near = self.pending.pop(user, None)
                if near:
                    vec, _, score = near
                    with GALLERY_LOCK:
                        store.Gallery(user).learn(vec, cfg.max_learned)
                    log.info("relearned %s: a scan scoring %.3f was followed by an unlock with "
                             "fingerprint or password", user, score)
                    history.record("relearn", user=user, best=round(score, 3))


def desktop_unlocked(user):
    """True if the user has an active, unlocked graphical session."""
    try:
        uid = pwd.getpwnam(user).pw_uid
        out = subprocess.run(["loginctl", "list-sessions", "--no-legend"],
                             capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[1] != str(uid):
                continue
            props = subprocess.run(
                ["loginctl", "show-session", parts[0], "-p", "Active", "-p", "LockedHint",
                 "-p", "Class", "-p", "Type"], capture_output=True, text=True, timeout=5).stdout
            p = dict(l.split("=", 1) for l in props.splitlines() if "=" in l)
            if (p.get("Active") == "yes" and p.get("LockedHint") == "no" and p.get("Class") == "user"
                    and p.get("Type") in ("wayland", "x11")):
                return True
    except (KeyError, OSError, subprocess.SubprocessError):
        pass
    return False


def peer_uid(conn):
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", creds)[1]


class Daemon:
    def __init__(self):
        self.engine = Engine()
        self.camera_lock = threading.Lock()
        self.lockouts = Lockouts()

    def serve(self, path=config.SOCKET_PATH):
        os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        srv.bind(path)
        os.umask(old)
        srv.listen(4)
        log.info("listening on %s (models loaded, cameras: %s)", path,
                 ", ".join(f"{p} {c}" for p, c in camera.candidates()) or "none")
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        with conn:
            try:
                if peer_uid(conn) not in (0, os.getuid()):
                    conn.sendall(b"UNAVAIL not allowed\n")
                    return
                conn.settimeout(5)
                line = conn.makefile("rb").readline(256).decode(errors="replace").strip()
                conn.settimeout(None)
                cmd, _, arg = line.partition(" ")
                if cmd == "AUTH":
                    try:
                        self._auth(conn, arg.strip())
                    except Exception as e:  # noqa: BLE001 - log it, never crash the service
                        log.exception("scan failed")
                        history.record("error", user=arg.strip()[:64], error=f"{type(e).__name__}: {e}"[:300])
                        try:
                            conn.sendall(b"UNAVAIL internal error\n")
                        except OSError:
                            pass
                elif cmd == "PING":
                    conn.sendall(b"OK\n")
                else:
                    conn.sendall(b"UNAVAIL unknown command\n")
            except (OSError, ValueError) as e:
                log.debug("client error: %s", e)

    def _auth(self, conn, user):
        def send(line):
            conn.sendall(line.encode() + b"\n")

        def emit(signal):
            try:
                send(f"MSG facelock:{signal}")
            except OSError:
                pass

        def alive():
            # the PAM side closes the socket when the lock screen cancels
            r, _, _ = select.select([conn], [], [], 0)
            if not r:
                return True
            try:
                return conn.recv(1, socket.MSG_PEEK) != b""
            except OSError:
                return False

        if not store.valid_user(user):
            send("UNAVAIL bad user")
            return
        gallery = store.Gallery(user)
        if not gallery.enrolled_any:
            send("UNAVAIL no face enrolled")
            return
        cfg = config.load()
        failures = self.lockouts.get(user)
        if failures >= cfg.max_failures:
            emit("paused")
            send("UNAVAIL face unlock paused after failed attempts")
            return
        if not self.camera_lock.acquire(timeout=3):
            send("UNAVAIL camera busy")
            return
        try:
            res = scan.run(self.engine, cfg, gallery, emit, alive,
                           failures_left=cfg.max_failures - failures)
        finally:
            self.camera_lock.release()
        total = self.lockouts.add(user, res.failures)
        learned = False
        if res.ok:
            self.lockouts.clear(user)
            if res.learn_vec is not None and (cfg.liveness != "blink" or res.blinked):
                with GALLERY_LOCK:
                    gallery.learn(res.learn_vec, cfg.max_learned)
                learned = True
        elif (res.best_vec is not None and res.best >= cfg.relearn_min
              and similarity(res.best_vec, gallery.enrolled) >= cfg.relearn_min - 0.05):
            # maybe it was you in bad light: learned if you unlock another way soon
            self.lockouts.remember(user, res.best_vec, res.best)
        log.info("auth %s: %s best=%.3f failures=%d/%d first_face=%s elapsed=%ss luma=%s dark=%s blink=%s learned=%s scores=%s",
                 user, res.reason, res.best, total if not res.ok else 0, cfg.max_failures,
                 res.first_face_s and round(res.first_face_s, 2), res.elapsed, res.luma, res.dark,
                 res.blinked, learned, res.scores[:12])
        history.record("scan", user=user, result=res.reason.split(":")[0], best=round(res.best, 3),
                       seconds=res.elapsed, first_face=res.first_face_s and round(res.first_face_s, 2),
                       failed=res.failures, dark=res.dark, blink=res.blinked, learned=learned,
                       blink_frames=res.blink_frames, blink_peak=res.blink_peak,
                       brightness=res.luma, **({"camera_error": res.reason} if res.reason.startswith("camera") else {}))
        try:
            if res.ok:
                emit("matched")
                send("OK")
            elif res.reason == "cancelled":
                send("FAIL cancelled")
            else:
                emit("paused" if total >= cfg.max_failures else f"failed:{res.reason}")
                send(f"FAIL {res.reason}")
        except OSError:
            pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    Daemon().serve()


if __name__ == "__main__":
    main()
