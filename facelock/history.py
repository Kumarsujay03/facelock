"""Scan history: one line per face scan (and per learning or error event) in
/var/lib/facelock/history.jsonl, root only. Kept for KEEP_DAYS, so after a
week or two of use `facelock stats` and `facelock report` show how face
unlock is doing on this laptop.

It holds times, outcomes, match scores, timings and brightness: no images
and no face data.
"""
import json
import os
import threading
import time

from .config import STATE_DIR

KEEP_DAYS = 14
PATH = os.path.join(STATE_DIR, "history.jsonl")
_lock = threading.Lock()


def record(kind, **fields):
    """Append one event: kind is scan | relearn | error."""
    entry = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, **fields}
    line = json.dumps(entry, separators=(",", ":")) + "\n"
    with _lock:
        try:
            os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
            fd = os.open(PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as f:
                f.write(line)
            _prune_daily()
        except OSError:
            pass


_last_prune = [0.0]


def _prune_daily():
    if time.time() - _last_prune[0] < 86400:
        return
    _last_prune[0] = time.time()
    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - KEEP_DAYS * 86400))
    keep = [e for e in read() if e.get("at", "") >= cutoff]
    tmp = PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        for e in keep:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")
    os.replace(tmp, PATH)


def read(days=None):
    try:
        with open(PATH) as f:
            entries = [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return []
    if days:
        cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - days * 86400))
        entries = [e for e in entries if e.get("at", "") >= cutoff]
    return entries


OUTCOME = {
    "matched": "unlocked",
    "failures": "not recognised",
    "timeout": "no face found",
    "cancelled": "unlocked another way",
}


def describe(e):
    """One readable line for an event."""
    if e["kind"] == "relearn":
        return f"{e['at']}  {e.get('user', '?'):<10} learned a near miss (score {e.get('best', 0):.2f}) after a fingerprint/password unlock"
    if e["kind"] == "error":
        return f"{e['at']}  {e.get('user', '-'):<10} ERROR {e.get('error', '')}"
    outcome = OUTCOME.get(e.get("result"), e.get("result", "?"))
    best = e.get("best")
    parts = [f"{e['at']}  {e.get('user', '?'):<10} {outcome:<21}",
             f"score {best:.2f}" if best is not None and best >= 0 else "score  -  ",
             f"{e.get('seconds', 0):5.2f}s"]
    if e.get("failed"):
        parts.append(f"{e['failed']} failed tr{'y' if e['failed'] == 1 else 'ies'}")
    if e.get("dark"):
        parts.append("dark: screen lit")
    if e.get("blink"):
        parts.append("blink seen")
    elif e.get("blink_frames"):
        parts.append(f"blink check {e['blink_frames']} frames, peak {e.get('blink_peak', 0):.1f}")
    if e.get("learned"):
        parts.append("learned")
    if e.get("brightness") is not None:
        parts.append(f"brightness {e['brightness']:.0f}")
    return "  ".join(parts)
