"""Record labelled blink data for tuning the blink check (no images saved).

Run as yourself, sitting in front of the laptop:
  ~/claude/facelock/.venv/bin/python -I ~/claude/facelock/tests/record_blinks.py

Follow the prompts: eyes open, blink on each "BLINK", then move your head
without blinking. Saves per-frame numbers (brightness, eye-patch contrast,
landmark positions) to tests/data/blinks-<time>.json.
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from facelock.camera import Camera  # noqa: E402
from facelock.engine import Engine  # noqa: E402

BLINKS = [5, 7.5, 10, 12.5, 15, 17.5]
PLAN = [(0, "Keep your eyes OPEN and look at the camera")] + \
       [(t, ">>> BLINK once <<<") for t in BLINKS] + \
       [(t + 0.6, "eyes open") for t in BLINKS] + \
       [(19.5, "Now turn your head slowly left and right. DON'T blink."), (26, "done")]
PLAN.sort()
END = 26


def eye_stats(gray, cx, cy, d):
    out = {}
    for name, kw, kh in (("s", 0.2, 0.12), ("m", 0.3, 0.2)):
        hw, hh = max(int(d * kw), 4), max(int(d * kh), 3)
        patch = gray[max(int(cy - hh), 0):int(cy + hh), max(int(cx - hw), 0):int(cx + hw)]
        if patch.size == 0:
            return None
        p = np.percentile(patch, (5, 10, 50, 90))
        out[name] = [round(float(v), 1) for v in (*p, patch.std())]
    return out


def main():
    eng = Engine()
    rows, step = [], 0
    with Camera() as cam:
        print("Starting camera...")
        for t, frame in cam.frames():
            if t < 0.8:
                continue
            t -= 0.8
            while step < len(PLAN) and t >= PLAN[step][0]:
                print(f"{PLAN[step][0]:5.1f}s  {PLAN[step][1]}", flush=True)
                step += 1
            if t > END:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            face = eng.detect(frame)
            row = {"t": round(t, 3), "luma": round(float(gray[::8, ::8].mean()), 1)}
            if face is not None:
                lm = face.landmarks
                d = float(np.hypot(*(lm[1] - lm[0])))
                row.update(box=[round(float(v), 1) for v in face.box], lm=lm.round(1).tolist(),
                           score=round(face.score, 3),
                           eyes=[eye_stats(gray, x, y, d) for x, y in lm[:2]])
            rows.append(row)
    os.makedirs(os.path.join(ROOT, "tests", "data"), exist_ok=True)
    path = os.path.join(ROOT, "tests", "data", time.strftime("blinks-%Y%m%d-%H%M%S.json"))
    with open(path, "w") as f:
        json.dump({"blinks_at": BLINKS, "move_from": 19.5, "frames": rows}, f)
    print(f"Saved {len(rows)} frames to {path}")


main()
