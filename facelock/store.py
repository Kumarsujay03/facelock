"""Per-user face data in /var/lib/facelock/<user>/ (root only).

Each face sample is kept twice, once per model, row for row:

enrolled.npy / enrolled_strong.npy   from enrolment, never changed by learning
learned2.npy / learned_strong.npy    added after strong unlocks, capped
others.npy / others_strong.npy       people who must NOT unlock (add-other)

SFace rows are 128 numbers, AuraFace ("strong") rows 512. An enrolment from
before AuraFace (facelock 1.1 or older) has no strong rows and must be redone.
learned.npy from facelock 1.0.x is deleted unread: its learning rules were
too loose and could take in a look-alike's face.
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


SFACE_DIM, STRONG_DIM = 128, 512


def _load(path, dim=SFACE_DIM):
    try:
        arr = np.load(path, allow_pickle=False)
        return arr.reshape(-1, dim).astype(np.float32)
    except (OSError, ValueError):
        return np.zeros((0, dim), np.float32)


class Gallery:
    def __init__(self, user):
        self.user = user
        self.dir = _dir(user)
        old = os.path.join(self.dir, "learned.npy")
        if os.path.exists(old):
            try:
                os.unlink(old)
            except OSError:
                pass
        self.enrolled = self._load("enrolled.npy")
        self.enrolled_strong = self._load("enrolled_strong.npy", STRONG_DIM)
        self.learned = self._load("learned2.npy")
        self.learned_strong = self._load("learned_strong.npy", STRONG_DIM)
        self.others = self._load("others.npy")
        self.others_strong = self._load("others_strong.npy", STRONG_DIM)
        # learned rows from before the strong model have no pair: drop them
        if len(self.learned) != len(self.learned_strong):
            self.learned = self.learned[:0]
            self.learned_strong = self.learned_strong[:0]
        self.meta = load_meta(user)
        # (drop, rise) from the blink calibration; older "blink_drop" values
        # came from a different measure and are ignored
        cal = self.meta.get("blink")
        self.blink = (float(cal["drop"]), float(cal["rise"])) if isinstance(cal, dict) else None

    def _load(self, name, dim=SFACE_DIM):
        return _load(os.path.join(self.dir, name), dim)

    @property
    def enrolled_any(self):
        return len(self.enrolled) > 0

    @property
    def strong_enrolled(self):
        """True if the enrolment has AuraFace samples (facelock 1.2+)."""
        return len(self.enrolled_strong) > 0

    def learn(self, vec, strong_vec, cap):
        """Add a sample (both models) from a strong unlock; when full, drop
        the one most like the others (it adds the least)."""
        # reload first: the service may have learned something since this
        # gallery was read
        learned = self._load("learned2.npy")
        strong = self._load("learned_strong.npy", STRONG_DIM)
        if len(learned) != len(strong):
            learned, strong = learned[:0], strong[:0]
        learned = np.vstack([learned, vec[None, :]])
        strong = np.vstack([strong, strong_vec[None, :]])
        if len(strong) > cap:
            sims = strong @ strong.T
            np.fill_diagonal(sims, -1)
            redundancy = sims.max(axis=1)
            redundancy[-1] = -2  # never drop the one just added
            drop = int(np.argmax(redundancy))
            learned = np.delete(learned, drop, axis=0)
            strong = np.delete(strong, drop, axis=0)
        self.learned, self.learned_strong = learned, strong
        _save(os.path.join(self.dir, "learned2.npy"), learned)
        _save(os.path.join(self.dir, "learned_strong.npy"), strong)

    def add_others(self, vectors, strong_vectors):
        self.others = np.vstack([self.others, np.asarray(vectors, np.float32)])
        self.others_strong = np.vstack([self.others_strong, np.asarray(strong_vectors, np.float32)])
        _save(os.path.join(self.dir, "others.npy"), self.others)
        _save(os.path.join(self.dir, "others_strong.npy"), self.others_strong)

    def clear_others(self):
        self.others = np.zeros((0, SFACE_DIM), np.float32)
        self.others_strong = np.zeros((0, STRONG_DIM), np.float32)
        for name in ("others.npy", "others_strong.npy"):
            try:
                os.unlink(os.path.join(self.dir, name))
            except FileNotFoundError:
                pass


def save_enrolment(user, embeddings, meta, strong=None):
    path = _dir(user, create=True)
    _save(os.path.join(path, "enrolled.npy"), np.asarray(embeddings))
    if strong is not None:
        _save(os.path.join(path, "enrolled_strong.npy"), np.asarray(strong))
    for name in ("learned.npy", "learned2.npy", "learned_strong.npy"):
        learned = os.path.join(path, name)
        if os.path.exists(learned):
            os.unlink(learned)  # learned data belongs to the old enrolment
    # a new enrolment starts the adaptive bar afresh
    update_meta(user, **meta, unlock_medians=[], enrolled_at=time.strftime("%Y-%m-%d %H:%M:%S"))


def record_unlock(user, median, keep=30):
    """Remember the AuraFace median of a face unlock (the adaptive bar)."""
    meta = load_meta(user)
    medians = (list(meta.get("unlock_medians", [])) + [round(float(median), 3)])[-keep:]
    update_meta(user, unlock_medians=medians)
    return medians


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
