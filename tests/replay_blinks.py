"""Replay a recording from record_blinks.py through the blink detector.

  .venv/bin/python -I tests/replay_blinks.py [recording.json]

Reports which prompted blinks are caught, and any false blink with the eyes
open or during the head turns.
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from facelock.liveness import BlinkDetector, calibrate  # noqa: E402


def features(frame):
    eyes = frame["eyes"]
    contrast = np.mean([(e["s"][3] - e["s"][1]) / (e["s"][3] + 1) for e in eyes])
    dark = np.mean([e["s"][1] for e in eyes])
    lm = np.asarray(frame["lm"])
    d = float(np.hypot(*(lm[1] - lm[0])))
    b = frame["box"]
    return contrast, dark, d, (b[0] + b[2] / 2, b[1] + b[3] / 2, lm[2][0]), frame["luma"]


def replay(frames, start, end, drop, rise):
    """Blinks found in [start, end): a fresh detector restarted after each,
    as each unlock scan starts fresh."""
    found, det = [], BlinkDetector(drop, rise)
    for f in frames:
        if not start <= f["t"] < end or "eyes" not in f or None in f["eyes"]:
            continue
        if det.feed(*features(f)):
            found.append(f["t"])
            det = BlinkDetector(drop, rise)
    return found


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob(os.path.join(ROOT, "tests/data/blinks-*.json")))[-1]
    rec = json.load(open(path))
    frames, blinks = rec["frames"], rec["blinks_at"]
    # calibration as enrolment does it: first 4.5 s open, then the blinks
    o, b = BlinkDetector(0, 99), BlinkDetector(0, 99)
    for f in frames:
        if "eyes" in f and None not in f["eyes"]:
            (o if f["t"] < 4.5 else b if f["t"] < 19 else BlinkDetector()).feed(*features(f))
    cal = calibrate(o.samples, b.samples)
    print(f"{os.path.basename(path)}: calibration -> {cal}")
    for drop, rise in [cal] if cal else []:
        found = replay(frames, 0, 19.3, drop, rise)
        hit = [bt for bt in blinks if any(bt - 0.1 <= t <= bt + 1.5 for t in found)]
        false = [t for t in found if not any(bt - 0.1 <= t <= bt + 1.5 for bt in blinks)]
        moving = replay(frames, 19.6, 99, drop, rise)
        print(f"  blinks caught {len(hit)}/{len(blinks)} at {[round(t, 2) for t in found]}")
        print(f"  false with eyes open: {false}   false during head turns: {moving}")


main()
