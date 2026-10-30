"""Per-user face data in /var/lib/facelock/<user>/ (root only).

Each face sample is kept twice, once per model, row for row:

enrolled.npy / enrolled_strong.npy   from enrolment, never changed by learning
enrolled_group.npy                   which capture (group) each enrolled row came from:
                                     0 = enrol, 1, 2... = each `facelock improve`;
                                     meta.json "groups" holds each group's bar data
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
        try:
            self.enrolled_group = np.load(os.path.join(self.dir, "enrolled_group.npy"),
                                          allow_pickle=False).astype(np.int32).reshape(-1)
        except (OSError, ValueError):
            self.enrolled_group = None
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
        if len(self.enrolled_strong) and (self.enrolled_group is None
                                          or len(self.enrolled_group) != len(self.enrolled_strong)
                                          or not isinstance(self.meta.get("groups"), dict)):
            self._split_into_groups()

    def _split_into_groups(self):
        """An enrolment from before groups (1.2.x): split its samples into
        groups by how they agree (good light, dim light, glasses...) and give
        each group the bar its own samples support, as a new capture would
        get. Saved once if possible (as root); else used for this run only."""
        from .scan import personal_bar, sessions_of
        legacy = float(sessions_of(self).get("0", {}).get("bar") or 0)  # the old single bar, capped
        ids, groups = auto_groups(self.enrolled_strong)
        for info in groups.values():
            # the group's own samples set its bar; it keeps the old (capped)
            # bar where its samples clearly support it (median - 0.05), so a
            # well-lit group stays as strict as before and only a weaker
            # condition (dim light) gets a lower one
            own = personal_bar(info["p10"]) if info.get("p10") else 0
            supported = min(legacy, info.get("median", 0) - 0.05)
            info["bar"] = round(max(own, supported), 3)
            info.pop("p10", None)
        self.enrolled_group = ids
        self.meta["groups"] = groups
        try:
            _save_ints(os.path.join(self.dir, "enrolled_group.npy"), ids)
            update_meta(self.user, groups=groups, adapted_bar=None, unlock_medians=None,
                        unlock_count=None, groups_from="auto-split of an older enrolment")
        except OSError:
            pass

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
    if strong is not None:
        _save_ints(os.path.join(path, "enrolled_group.npy"), np.zeros(len(strong), np.int32))
    else:
        try:
            os.unlink(os.path.join(path, "enrolled_group.npy"))
        except FileNotFoundError:
            pass
    # a new enrolment starts the groups and their adaptive bars afresh
    group = {"p10": meta["strong_self_p10"]} if meta.get("strong_self_p10") else {}
    update_meta(user, **meta, groups={"0": group}, unlock_medians=None, unlock_count=None,
                adapted_bar=None, enrolled_at=time.strftime("%Y-%m-%d %H:%M:%S"))


GROUP_JOIN = 0.60     # samples (groups) this similar on average are one condition
GROUP_MIN = 4         # smaller groups join their closest neighbour


def _self_scores(vecs):
    """Each sample's mean similarity to its 3 closest others in the group."""
    sims = vecs @ vecs.T
    np.fill_diagonal(sims, -1)
    k = min(3, len(vecs) - 1)
    return np.sort(sims, axis=1)[:, ::-1][:, :k].mean(axis=1)


def _self_p10(vecs):
    """How closely a group's samples match the rest of the group: the 10th
    percentile, over its samples, of each one's mean similarity to its 3
    closest others (the same measure a capture's bar comes from, judged
    conservatively: these samples were picked to differ from each other)."""
    if len(vecs) < 2:
        return None
    return round(float(np.percentile(_self_scores(vecs), 10)), 3)


def auto_groups(strong):
    """Group samples by condition: average-linkage clustering on AuraFace
    similarity, joining while groups agree at GROUP_JOIN on average, then
    folding groups under GROUP_MIN samples into their closest group. Returns
    (group id per row, {group id: {"p10": ...}})."""
    strong = np.asarray(strong, np.float32)
    n = len(strong)
    clusters = [[i] for i in range(n)]
    sims = strong @ strong.T

    def link(a, b):
        return float(sims[np.ix_(a, b)].mean())

    while len(clusters) > 1:
        best, pair = -2.0, None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                s = link(clusters[i], clusters[j])
                if s > best:
                    best, pair = s, (i, j)
        if best < GROUP_JOIN:
            break
        i, j = pair
        clusters[i] += clusters.pop(j)
    while len(clusters) > 1 and min(len(c) for c in clusters) < GROUP_MIN:
        small = min(range(len(clusters)), key=lambda c: len(clusters[c]))
        rest = [c for c in range(len(clusters)) if c != small]
        to = max(rest, key=lambda c: link(clusters[small], clusters[c]))
        clusters[to] = clusters[to] + clusters[small]
        clusters.pop(small)
    clusters.sort(key=len, reverse=True)
    ids = np.zeros(n, np.int32)
    groups = {}
    for gid, rows in enumerate(clusters):
        ids[rows] = gid
        p10 = _self_p10(strong[rows])
        median = round(float(np.median(_self_scores(strong[rows]))), 3) if len(rows) > 1 else None
        groups[str(gid)] = {k: v for k, v in (("p10", p10), ("median", median)) if v}
    return ids, groups


def _save_ints(path, array):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            np.save(f, np.asarray(array, np.int32))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _groups(g):
    """The gallery's group ids (one per enrolled row) and group info."""
    from .scan import sessions_of
    ids = g.enrolled_group
    if ids is None or len(ids) != len(g.enrolled_strong):
        ids = np.zeros(len(g.enrolled_strong), np.int32)
    return np.asarray(ids, np.int32), {k: dict(v) for k, v in sessions_of(g).items()}


ENROLLED_CAP = 48


def add_enrolment(user, vectors, strong, p10=None):
    """Add a capture to an existing enrolment (facelock improve) as a new
    group with its own bar (from p10, how closely the capture matched
    itself), keeping the learned samples and the other groups' bars. When
    there are more than ENROLLED_CAP samples, the most varied set is kept
    (farthest-point on AuraFace), starting from the new ones.
    Returns (sample count, new group id)."""
    g = Gallery(user)
    ids, groups = _groups(g)
    new_id = int(max([int(k) for k in groups] + [int(i) for i in ids] + [-1])) + 1
    weak = np.vstack([g.enrolled, np.asarray(vectors, np.float32)])
    strong_all = np.vstack([g.enrolled_strong, np.asarray(strong, np.float32)])
    ids = np.concatenate([ids, np.full(len(strong), new_id, np.int32)])
    if len(strong_all) > ENROLLED_CAP:
        chosen = [len(g.enrolled_strong)]  # start from the first new sample
        while len(chosen) < ENROLLED_CAP:
            sims = (strong_all @ strong_all[chosen].T).max(axis=1)
            sims[chosen] = 2
            chosen.append(int(np.argmin(sims)))
        weak, strong_all, ids = weak[chosen], strong_all[chosen], ids[chosen]
    groups[str(new_id)] = {"p10": round(float(p10), 3)} if p10 else {}
    groups = {k: v for k, v in groups.items() if int(k) in set(int(i) for i in ids)}
    _save(os.path.join(g.dir, "enrolled.npy"), weak)
    _save(os.path.join(g.dir, "enrolled_strong.npy"), strong_all)
    _save_ints(os.path.join(g.dir, "enrolled_group.npy"), ids)
    update_meta(user, groups=groups)
    return len(strong_all), new_id


def record_unlock(user, median, group, current_bar, keep=30):
    """Remember the AuraFace median of a face unlock in the group it matched
    and move that group's adaptive bar (scan.next_bar): good-light unlocks
    tighten the good-light bar, never the dim-light one. Returns the bar."""
    from .scan import next_bar
    _, groups = _groups(Gallery(user))
    info = groups.setdefault(str(group), {})
    medians = list(info.get("unlock_medians", [])) + [round(float(median), 3)]
    count = int(info.get("unlock_count", len(medians) - 1)) + 1
    bar = next_bar(current_bar, [0.0] * max(0, count - len(medians)) + medians)
    info.update(unlock_medians=medians[-keep:], unlock_count=count, adapted=bar)
    update_meta(user, groups=groups)
    return bar


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
