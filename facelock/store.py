"""Per-user face data in /var/lib/facelock/<user>/ (root only).

enrolled.npy  embeddings from enrolment, never changed by learning
learned.npy   embeddings added after confident unlocks, capped
"""
import json
import os
import re
import tempfile
import time

import numpy as np

from .config import STATE_DIR

USER_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,63}$", re.I)


def valid_user(user):
    return bool(user) and USER_RE.match(user) is not None


def _dir(user, create=False):
    if not valid_user(user):
        raise ValueError(f"invalid user name {user!r}")
    path = os.path.join(STATE_DIR, user)
    if create:
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
        os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _save(path, array):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            np.save(f, array.astype(np.float32))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _load(path):
    try:
        arr = np.load(path, allow_pickle=False)
    except (OSError, ValueError):
        return np.zeros((0, 128), np.float32)
    return arr.reshape(-1, 128).astype(np.float32)


class Gallery:
    def __init__(self, user):
        self.user = user
        self.dir = _dir(user)
        self.enrolled = _load(os.path.join(self.dir, "enrolled.npy"))
        self.learned = _load(os.path.join(self.dir, "learned.npy"))
        self.meta = load_meta(user)
        # (drop, rise) from the blink calibration; older "blink_drop" values
        # came from a different measure and are ignored
        cal = self.meta.get("blink")
        self.blink = (float(cal["drop"]), float(cal["rise"])) if isinstance(cal, dict) else None

    @property
    def enrolled_any(self):
        return len(self.enrolled) > 0

    @property
    def all(self):
        return np.vstack([self.enrolled, self.learned]) if len(self.learned) else self.enrolled

    def learn(self, vec, cap):
        """Add an embedding from a confident unlock; when full, drop the one
        most like the others (it adds the least)."""
        # reload first: the service may have learned something since this
        # gallery was read
        self.learned = _load(os.path.join(self.dir, "learned.npy"))
        learned = np.vstack([self.learned, vec[None, :]])
        if len(learned) > cap:
            sims = learned @ learned.T
            np.fill_diagonal(sims, -1)
            # never drop the one just added
            redundancy = sims.max(axis=1)
            redundancy[-1] = -2
            learned = np.delete(learned, int(np.argmax(redundancy)), axis=0)
        self.learned = learned
        _save(os.path.join(self.dir, "learned.npy"), learned)


def save_enrolment(user, embeddings, meta):
    path = _dir(user, create=True)
    _save(os.path.join(path, "enrolled.npy"), np.asarray(embeddings))
    learned = os.path.join(path, "learned.npy")
    if os.path.exists(learned):
        os.unlink(learned)  # learned data belongs to the old enrolment
    update_meta(user, **meta, enrolled_at=time.strftime("%Y-%m-%d %H:%M:%S"))


def load_meta(user):
    try:
        with open(os.path.join(_dir(user), "meta.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def update_meta(user, **fields):
    meta = dict(load_meta(user), **fields)
    path = os.path.join(_dir(user, create=True), "meta.json")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(meta, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def remove(user):
    path = _dir(user)
    if not os.path.isdir(path):
        return False
    for name in os.listdir(path):
        os.unlink(os.path.join(path, name))
    os.rmdir(path)
    return True


def users():
    try:
        return sorted(u for u in os.listdir(STATE_DIR)
                      if valid_user(u) and os.path.exists(os.path.join(STATE_DIR, u, "enrolled.npy")))
    except OSError:
        return []
