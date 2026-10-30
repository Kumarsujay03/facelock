"""One face unlock scan: camera on, match, blink check, camera off.

The caller passes `emit(signal)` to report progress to the lock screen
(signals are sent as "facelock:<signal>" PAM info messages) and `alive()`
to stop early when the lock screen gives up on the scan.
"""
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .camera import Camera
from .engine import luma, similarity
from .liveness import BlinkDetector


@dataclass
class Result:
    ok: bool = False
    reason: str = ""          # matched | timeout | failures | cancelled | camera
    failures: int = 0         # attempts where a face was seen but did not match
    best: float = -1.0
    best_vec: np.ndarray = None
    learn_vec: np.ndarray = None
    blinked: bool = False
    other_seen: bool = False  # a face closer to a known other person was refused
    blink_frames: int = 0     # frames the blink check could measure
    blink_peak: float = 0.0   # its strongest eye-closure score (2.0 counts as shut)
    dark: bool = False
    first_face_s: float = None
    elapsed: float = 0.0
    scores: list = field(default_factory=list)
    luma: float = None


def frame_score(vec, gallery, cfg):
    """(score, enrolled score, refused as an 'other') for one face.

    The score is the similarity to the enrolment; learned samples (other
    light, angles) only raise it for a face already close to the enrolment,
    so they can't carry a look-alike over the bar. A face at least as close
    to a known other person (facelock add-other) is refused."""
    enrolled_score = similarity(vec, gallery.enrolled)
    score = enrolled_score
    if enrolled_score >= cfg.learned_floor and len(gallery.learned):
        score = max(score, similarity(vec, gallery.learned))
    if len(gallery.others) and similarity(vec, gallery.others) >= score - cfg.other_margin:
        return min(score, 0.0), enrolled_score, True
    return score, enrolled_score, False


def accepted(hits, score, enrolled_score, cfg):
    """Enough evidence to unlock: accept_frames frames at or above accept,
    or one frame at or above accept_instant that is also close to the
    enrolment itself."""
    return hits >= cfg.accept_frames or (score >= cfg.accept_instant and enrolled_score >= cfg.accept)


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
    hits = 0                  # frames >= threshold in this attempt
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
                    attempt_start, hits, matched_at = None, 0, None
                    blink.reset()
                continue
            if res.first_face_s is None:
                res.first_face_s = t
            last_face = t
            if attempt_start is None:
                attempt_start, hits = t, 0
            vec = engine.embed(frame, face)
            score, enrolled_score, other = frame_score(vec, gallery, cfg)
            res.other_seen = res.other_seen or other
            res.scores.append(round(score, 3))
            if score > res.best:
                res.best, res.best_vec = score, vec
            if enrolled_score >= cfg.learn_from and score > 0:
                # most novel strong frame: teaches the gallery the most
                if res.learn_vec is None or similarity(vec, gallery.learned) < similarity(res.learn_vec, gallery.learned):
                    res.learn_vec = vec
            if watch_blink:
                res.blinked = blink.update(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), face)
            if verbose:
                verbose(t, score, face, lum, res.blinked)
            if trace is not None:
                trace.append(dict(t=round(t, 3), score=round(score, 3), luma=round(lum, 1),
                                  face=round(face.size), box=[round(float(v)) for v in face.box],
                                  lm=face.landmarks.round().tolist(), blinked=res.blinked, **blink.last))

            if score >= cfg.accept:
                hits += 1
            matched = accepted(hits, score, enrolled_score, cfg)
            if matched and matched_at is None:
                matched_at = t
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
                attempt_start, hits = None, 0
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
    if not res.reason:
        res.reason = "timeout"
    return res
