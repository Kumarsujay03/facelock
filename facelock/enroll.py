"""Enrolment: capture a few seconds of your face from slightly different
angles and keep a diverse set of embeddings."""
import time

import cv2
import numpy as np

from .camera import Camera
from .engine import sharpness
from .liveness import BlinkDetector, calibrate

STEPS = [
    (3.0, "Look straight at the camera"),
    (2.0, "Turn your head slightly left"),
    (2.0, "Turn your head slightly right"),
    (2.0, "Tilt your chin slightly up"),
    (2.0, "Tilt your chin slightly down"),
    (2.0, "Look straight again"),
]
KEEP = 16


def pick_diverse(vectors, n):
    """Farthest-point selection, starting from the most central one."""
    vectors = np.asarray(vectors)
    centre = vectors.mean(axis=0)
    chosen = [int(np.argmax(vectors @ centre))]
    while len(chosen) < min(n, len(vectors)):
        sims = (vectors @ vectors[chosen].T).max(axis=1)
        sims[chosen] = 2
        chosen.append(int(np.argmin(sims)))
    return chosen


def capture(engine, cfg, say=print):
    """Returns (SFace samples, AuraFace samples, details). The details
    include strong_accept: this face's own AuraFace bar, from how closely the
    frames not kept match the kept ones (never below cfg.strong_accept)."""
    vectors, crops, sizes, sharp, frames = [], [], [], [], 0
    with Camera(cfg.device) as cam:
        say(f"Camera: {cam.path}")
        stream = cam.frames()
        # let the exposure settle
        for t, _ in stream:
            if t > 0.8:
                break
        for seconds, text in STEPS:
            say(text)
            end = time.monotonic() + seconds
            got = 0
            for _, frame in stream:
                if time.monotonic() > end:
                    break
                frames += 1
                face = engine.detect(frame)
                if face is None or face.score < 0.85 or face.size < cfg.min_face_px:
                    continue
                crop = engine.align(frame, face)
                vectors.append(engine.embed(frame, face, crop))
                crops.append(crop)
                sizes.append(face.size)
                sharp.append(sharpness(frame, face))
                got += 1
            if not got:
                say("  (no clear face seen in this step)")
    if len(vectors) < 10:
        raise RuntimeError(f"only {len(vectors)} clear face frames out of {frames}; "
                           "sit in front of the camera in good light and try again")
    # drop motion-blurred frames: sharpness is relative, cameras differ a lot
    sharp = np.asarray(sharp)
    good = np.flatnonzero(sharp >= 0.6 * np.median(sharp))
    vectors = np.asarray(vectors)[good]
    crops = [crops[i] for i in good]
    centre = vectors.mean(axis=0)
    centre /= np.linalg.norm(centre)
    agree = vectors @ centre
    if np.median(agree) < 0.55:
        raise RuntimeError("the frames don't agree with each other (more than one person in view?)")
    ok = np.flatnonzero(agree >= 0.45)
    chosen = [int(ok[i]) for i in pick_diverse(vectors[ok], KEEP)]
    say("Analysing the face (this takes a few seconds)...")
    keep_strong = np.array([engine.embed_strong(None, None, crops[i]) for i in chosen])
    if float((keep_strong @ keep_strong.mean(axis=0)).min()) < 0.3:
        raise RuntimeError("the frames don't agree with each other (more than one person in view?)")
    # how closely this face matches itself: frames not kept, against the kept
    rest = [i for i in range(len(crops)) if i not in set(chosen)]
    rest = rest[::max(1, len(rest) // 40)][:40]
    self_scores = []
    for i in rest:
        v = engine.embed_strong(None, None, crops[i])
        self_scores.append(float(np.mean(np.sort(keep_strong @ v)[::-1][:3])))
    # the bar sits below how this face matches itself (other light and days
    # score lower than one sitting), and never below the floor (scan.py)
    from .scan import personal_bar
    p10 = float(np.percentile(self_scores, 10)) if self_scores else 0.0
    strong_accept = max(personal_bar(p10), cfg.strong_accept)
    return vectors[chosen], keep_strong, {
        "frames": frames, "faces": len(vectors), "kept": len(chosen),
        "median_face_px": float(np.median(sizes)),
        "self_similarity": round(float(np.median(agree)), 3),
        "strong_self_p10": round(p10, 3), "strong_accept": strong_accept}


def calibrate_blink(engine, cfg, say=print):
    """Measure how much this camera sees your eyes change when you blink.
    Returns ((drop, rise) or None if blinks can't be told apart, details)."""
    phases = [(3.5, "Keep your eyes open and look at the camera", []),
              (5.0, "Now blink slowly, 3 times", [])]
    with Camera(cfg.device) as cam:
        stream = cam.frames()
        for t, _ in stream:
            if t > 0.8:
                break
        for seconds, text, ratios in phases:
            say(text)
            detector = BlinkDetector(drop=0.0, rise=999)  # never fires: just records
            end = time.monotonic() + seconds
            for _, frame in stream:
                if time.monotonic() > end:
                    break
                face = engine.detect(frame)
                if face is not None:
                    detector.update(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), face)
            ratios.extend(detector.samples)
    open_s, blink_s = phases[0][2], phases[1][2]
    return calibrate(open_s, blink_s), {
        "open_floor": round(float(np.percentile([r for r, _ in open_s], 1)), 3) if open_s else None,
        "blink_dips": sorted(r for r, _ in blink_s)[:3]}
