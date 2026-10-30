"""One face unlock scan: camera on, match, blink check, camera off.

The decision: two independent face models, over several frames.

1. Every frame with a face is described by SFace (fast). Frames that look
   nothing like the user are skipped.
2. The rest are also described by AuraFace (slower, much better at telling
   similar faces apart).
3. It unlocks only when the last `strong_frames` such frames (5 by default)
   all show the same person, AuraFace's median similarity to the enrolment
   reaches the user's bar, no single frame is far below it, and SFace's
   median also reaches its bar. One lucky frame can't do it, and a face has
   to fool both models in every one of those frames.

The caller passes `emit(signal)` to report progress to the lock screen
(signals are sent as "facelock:<signal>" PAM info messages) and `alive()`
to stop early when the lock screen gives up on the scan.
"""
from dataclasses import dataclass, field

import cv2
import numpy as np

from .camera import Camera
from .engine import luma, similarity, top_similarity
from .liveness import BlinkDetector


@dataclass
class Result:
    ok: bool = False
    reason: str = ""          # matched | timeout | failures | cancelled | camera
    failures: int = 0         # attempts where a face was seen but did not match
    best: float = -1.0        # best SFace frame score
    best_strong: float = -1.0  # best AuraFace frame score
    median_strong: float = None  # AuraFace median of the deciding frames
    group: int = None         # the sample group they matched best
    group_bar: float = None
    learn_vec: np.ndarray = None
    learn_strong: np.ndarray = None
    blinked: bool = False
    other_seen: bool = False  # a face closer to a known other person was refused
    blink_frames: int = 0     # frames the blink check could measure
    blink_peak: float = 0.0   # its strongest eye-closure score (2.0 counts as shut)
    dark: bool = False
    first_face_s: float = None
    elapsed: float = 0.0
    scores: list = field(default_factory=list)         # SFace per frame
    strong_scores: list = field(default_factory=list)  # AuraFace per checked frame
    luma: float = None


def frame_score(vec, gallery, cfg):
    """SFace: (score, enrolled score, refused as an 'other') for one face.

    Learned samples (other light, angles) only raise the score of a face
    already close to the enrolment. A face at least as close to a known other
    person (facelock add-other) is refused."""
    enrolled_score = similarity(vec, gallery.enrolled)
    score = enrolled_score
    if enrolled_score >= cfg.learned_floor and len(gallery.learned):
        score = max(score, similarity(vec, gallery.learned))
    if len(gallery.others) and similarity(vec, gallery.others) >= score - cfg.other_margin:
        return min(score, 0.0), enrolled_score, True
    return score, enrolled_score, False


def groups_of(gallery):
    """The enrolment's sample groups: {group id: (row indices, group info)}.
    Each capture (the enrolment, every `facelock improve`) is a group with
    its own bar, because a face in dim light matches dim-light samples less
    closely than a face in good light matches good-light samples."""
    ids = getattr(gallery, "enrolled_group", None)
    n = len(getattr(gallery, "enrolled_strong", ()))
    if ids is None or len(ids) != n:
        ids = np.zeros(n, np.int32)
    info = sessions_of(gallery)
    present = sorted(set(int(i) for i in ids)) or sorted(int(k) for k in info)
    return {g: (np.flatnonzero(ids == g), info.get(str(g), {})) for g in present}


def sessions_of(gallery):
    """Per-group info from the face data's meta.json. Enrolments made before
    groups existed (1.2.7 and older) become one group whose bar is the one
    measured at enrolment: 1.2.7's `improve` could raise the old single bar
    from a good-light capture, which then refused the user in dim light."""
    meta = gallery.meta
    if isinstance(meta.get("groups"), dict):
        return meta["groups"]
    legacy = {}
    if meta.get("strong_self_p10"):
        # at most 0.70: a 1.2.7 `improve` may have raised it from a single
        # well-lit capture. Enrolling again gives proper per-group bars.
        legacy["bar"] = min(personal_bar(float(meta["strong_self_p10"])), 0.70)
    if meta.get("adapted_bar"):
        legacy["adapted"] = min(float(meta["adapted_bar"]), 0.70)
    return {"0": legacy}


def group_bar(info, cfg):
    """A group's AuraFace bar: measured from its own capture (or kept from an
    older enrolment), raised by its own unlocks, never below STRONG_FLOOR or
    the configured minimum."""
    bar = max(STRONG_FLOOR, cfg.strong_accept)
    if info.get("p10"):
        bar = max(bar, personal_bar(float(info["p10"])))
    elif info.get("bar"):
        bar = max(bar, float(info["bar"]))
    if info.get("adapted"):
        bar = max(bar, float(info["adapted"]))
    return round(bar, 3)


def strong_bars(gallery, cfg):
    """{group id: bar}"""
    return {g: group_bar(info, cfg) for g, (_, info) in groups_of(gallery).items()}


def strong_score(vec, gallery, cfg):
    """AuraFace: (score, enrolled score, refused as an 'other', group, bar).

    The face is scored against each sample group (mean of its k closest
    samples) and judged by the group where it clears that group's bar by the
    most; that group's bar is the bar for this frame."""
    best = None
    for g, (rows, info) in groups_of(gallery).items():
        if not len(rows):
            continue
        s = top_similarity(vec, gallery.enrolled_strong[rows])
        bar = group_bar(info, cfg)
        if best is None or s - bar > best[0] - best[2]:
            best = (s, g, bar)
    enrolled_score, group, bar = best
    score = enrolled_score
    if enrolled_score >= cfg.strong_learned_floor and len(gallery.learned_strong):
        score = max(score, top_similarity(vec, gallery.learned_strong))
    if len(gallery.others_strong) and similarity(vec, gallery.others_strong) >= score - cfg.other_margin:
        return min(score, 0.0), enrolled_score, True, group, bar
    return score, enrolled_score, False, group, bar


# The lowest AuraFace bar anyone gets, whatever the settings say (they can
# only raise it). Strangers stayed below 0.35 on LFW; a look-alike brother
# reached 0.55 in real use.
STRONG_FLOOR = 0.62
# SFace's median bar has a floor too (the brother reached 0.54-0.57 on it)
SFACE_FLOOR = 0.58


def personal_bar(self_p10):
    """A face's own AuraFace bar from enrolment: how closely it matches itself
    (90% of frames at or above self_p10) minus a margin for other light and
    days, kept between the floor and 0.75. (1.2.1-1.2.5 used a 0.12 margin; a
    look-alike brother wearing glasses then came within 0.04 of the bar.)"""
    return round(min(max(self_p10 - 0.08, STRONG_FLOOR), 0.75), 3)


# The bar adapts to the user's own unlocks, slowly: it only collects their
# scores for the first ADAPT_AFTER unlocks, and then only rises while their
# recent median is at least ADAPT_MIN_MEDIAN, by at most ADAPT_STEP per
# unlock, towards that median minus ADAPT_MARGIN (and below their typical
# worst unlock), never above ADAPT_CAP. It never goes down.
ADAPT_AFTER = 20
ADAPT_KEEP = 30       # unlock medians remembered
ADAPT_MIN_MEDIAN = 0.70
ADAPT_MARGIN = 0.12
ADAPT_STEP = 0.005
ADAPT_CAP = 0.75


def next_bar(current, medians):
    """The bar after one more unlock (medians includes it)."""
    recent = medians[-ADAPT_KEEP:]
    if len(medians) < ADAPT_AFTER or float(np.median(recent)) < ADAPT_MIN_MEDIAN:
        return current
    target = min(float(np.median(recent)) - ADAPT_MARGIN,
                 float(np.percentile(recent, 10)) - 0.05, ADAPT_CAP)
    if target <= current:
        return current
    return round(min(current + ADAPT_STEP, target), 3)


def strong_bar(gallery, cfg, group=None):
    """The AuraFace bar of one group (by default the strictest)."""
    bars = strong_bars(gallery, cfg)
    if group is not None and group in bars:
        return bars[group]
    return max(bars.values()) if bars else max(STRONG_FLOOR, cfg.strong_accept)


def decide(window, cfg, bar=None):
    """window: the latest checked frames as (sface score, strong score,
    strong vector[, that frame's bar]). True if they prove it is the user:
    the median margin over each frame's bar is not negative, no frame is
    more than strong_spread below its bar, all frames show one person, and
    SFace agrees."""
    if len(window) < cfg.strong_frames:
        return False
    recent = window[-cfg.strong_frames:]
    sface = np.array([w[0] for w in recent])
    margin = np.array([w[1] - (w[3] if len(w) > 3 else bar) for w in recent])
    vecs = np.array([w[2] for w in recent])
    same_person = float((vecs @ vecs.T).min()) >= cfg.strong_same_person
    return (same_person
            and float(np.median(margin)) >= 0
            and float(margin.min()) >= -cfg.strong_spread
            and float(np.median(sface)) >= max(cfg.accept, SFACE_FLOOR))


def run(engine, cfg, gallery, emit=lambda s: None, alive=lambda: True,
        failures_left=None, verbose=None, trace=None):
    res = Result()
    failures_left = cfg.max_failures if failures_left is None else failures_left
    need_blink = cfg.liveness == "blink"
    watch_blink = cfg.liveness in ("blink", "observe")  # observe: recorded, never waited for
    blink = BlinkDetector(*gallery.blink) if gallery.blink else BlinkDetector()
    deadline = cfg.scan_seconds
    attempt_start = None      # time the current attempt saw its first face
    last_face = None
    window = []               # checked frames of this attempt
    matched_at = None
    lumas = []
    live = []                 # brightness of frames since the picture started
    live_from = None
    blink_asked = False
    t = 0.0
    try:
        cam = Camera(cfg.device).open()
    except Exception as e:  # noqa: BLE001 - any camera failure ends the scan
        res.reason = f"camera: {e}"
        return res
    emit("scanning")
    try:
        for t, frame in cam.frames():
            if not alive():
                res.reason = "cancelled"
                break
            if t > deadline:
                res.reason = res.reason or "timeout"
                break
            lum = luma(frame)
            lumas.append(lum)
            # dark room: light the face with the screen, and give the camera
            # longer. Only as a second try: once the picture has really
            # started (right after boot the first frames are pure black for a
            # while) and the camera has had cfg.light_after seconds to find
            # and match a face in the room's own light.
            if lum >= 2.0 or t > 1.5:
                live.append(lum)
                live_from = t if live_from is None else live_from
            if (cfg.lowlight and not res.dark and len(live) >= 10
                    and t - live_from >= cfg.light_after
                    and float(np.median(live[-5:])) < cfg.dark_luma
                    and res.best < cfg.accept):
                res.dark = True
                emit("dark")
                deadline += 2.5
            face = engine.detect(frame)
            if face is None or face.size < cfg.min_face_px:
                # face lost for a while: the attempt ends without counting
                if attempt_start is not None and last_face is not None and t - last_face > 1.0:
                    attempt_start, window, matched_at = None, [], None
                    blink.reset()
                continue
            if res.first_face_s is None:
                res.first_face_s = t
            last_face = t
            if attempt_start is None:
                attempt_start, window = t, []
            crop = engine.align(frame, face)
            vec = engine.embed(frame, face, crop)
            score, enrolled_score, other = frame_score(vec, gallery, cfg)
            res.other_seen = res.other_seen or other
            res.scores.append(round(score, 3))
            res.best = max(res.best, score)
            strong = None
            if score >= cfg.sface_gate:
                # worth the strong model's time
                strong_vec = engine.embed_strong(frame, face, crop)
                strong, strong_enrolled, other, group, bar = strong_score(strong_vec, gallery, cfg)
                res.other_seen = res.other_seen or other
                res.strong_scores.append(round(strong, 3))
                res.best_strong = max(res.best_strong, strong)
                window.append((score, strong, strong_vec, bar, group))
                if (strong_enrolled >= cfg.learn_strong and enrolled_score >= cfg.learn_from
                        and not other and (res.learn_strong is None
                                           or similarity(strong_vec, gallery.learned_strong)
                                           < similarity(res.learn_strong, gallery.learned_strong))):
                    # most novel strong frame: teaches the gallery the most
                    res.learn_vec, res.learn_strong = vec, strong_vec
            elif window:
                # a frame that looks nothing like the user breaks the run
                window = []
            if watch_blink:
                res.blinked = blink.update(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), face)
            if verbose:
                verbose(t, score, strong, face, lum, res.blinked)
            if trace is not None:
                trace.append(dict(t=round(t, 3), score=round(score, 3),
                                  strong=None if strong is None else round(strong, 3),
                                  luma=round(lum, 1), face=round(face.size),
                                  box=[round(float(v)) for v in face.box],
                                  lm=face.landmarks.round().tolist(), blinked=res.blinked, **blink.last))

            if matched_at is None and decide(window, cfg):
                matched_at = t
                recent = window[-cfg.strong_frames:]
                res.median_strong = round(float(np.median([w[1] for w in recent])), 3)
                groups = [w[4] for w in recent]
                res.group = max(set(groups), key=groups.count)
                res.group_bar = [w[3] for w in recent if w[4] == res.group][0]
            if matched_at is not None:
                if not need_blink or res.blinked:
                    res.ok, res.reason = True, "matched"
                    break
                if not blink_asked:
                    blink_asked = True
                    emit("blink")
                    deadline = max(deadline, t + 4.0)
                continue
            if t - attempt_start >= cfg.attempt_seconds:
                res.failures += 1
                emit(f"retry:{res.failures}:{failures_left}")
                if res.failures >= failures_left:
                    res.reason = "failures"
                    break
                attempt_start, window = None, []
                blink.reset()
    except RuntimeError as e:
        res.reason = f"camera: {e}"
    finally:
        cam.close()
        if res.dark:
            emit("light")
    res.elapsed = round(t, 2)
    res.blink_frames = len(blink.samples)
    res.blink_peak = round(blink.peak, 2)
    res.luma = round(float(np.median(lumas)), 1) if lumas else None
    if not res.ok:
        res.learn_vec = res.learn_strong = None
    if not res.reason:
        res.reason = "timeout"
    return res
