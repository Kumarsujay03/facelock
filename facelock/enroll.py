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
    return vectors[chosen]


def capture(engine, cfg, say=print):
    vectors, sizes, sharp, frames = [], [], [], 0
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
                vectors.append(engine.embed(frame, face))
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
    vectors = np.asarray(vectors)[sharp >= 0.6 * np.median(sharp)]
    centre = vectors.mean(axis=0)
    centre /= np.linalg.norm(centre)
    agree = vectors @ centre
    if np.median(agree) < 0.55:
        raise RuntimeError("the frames don't agree with each other (more than one person in view?)")
    keep = pick_diverse(vectors[agree >= 0.45], KEEP)
    return keep, {"frames": frames, "faces": len(vectors), "kept": len(keep),
                  "median_face_px": float(np.median(sizes)),
                  "self_similarity": round(float(np.median(agree)), 3)}


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
