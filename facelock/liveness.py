"""Blink check: a printed or on-screen photo can't blink.

Two things change in a small patch around each eye landmark when the lids
close: the contrast drops (no dark pupil and lashes against the white of the
eye) and the darkest pixels get lighter (skin instead of pupil). A frame
counts as "closed" when the two together are clearly beyond what open eyes
do, relative to the last half second (they peak a frame apart, so they're
added up rather than required on the same frame). A blink is 1-8 closed frames followed by open eyes,
while the head stays still and the exposure is steady (head turns and
brightness changes alter the patches even more than a blink does).

The thresholds depend on the camera and the person, so enrolment measures
them (calibrate) and stores them with the face data.
"""
import numpy as np

DEFAULT_DROP = 0.88   # contrast must fall below this fraction of its recent level
DEFAULT_RISE = 4.0    # and the darkest pixels must get this much lighter (0-255)


def eye_features(gray, face):
    """(contrast, darkness, interocular distance, head position, face
    brightness), or None if an eye is off-frame. The head position is the face
    box centre and the nose's x: the detector's landmarks jump upwards while
    the eyes are shut, the box and the nose's sideways position don't."""
    lm = face.landmarks
    (rx, ry), (lx, ly) = lm[0], lm[1]
    d = float(np.hypot(lx - rx, ly - ry))
    hw, hh = max(int(d * 0.2), 4), max(int(d * 0.12), 3)
    contrast, dark = [], []
    for cx, cy in ((rx, ry), (lx, ly)):
        x0, y0, x1, y1 = int(cx - hw), int(cy - hh), int(cx + hw), int(cy + hh)
        if x0 < 0 or y0 < 0 or x1 > gray.shape[1] or y1 > gray.shape[0]:
            return None
        p10, p90 = np.percentile(gray[y0:y1, x0:x1], (10, 90))
        contrast.append((p90 - p10) / (p90 + 1.0))
        dark.append(p10)
    nx, ny = lm[2]
    # whole-frame brightness: auto-exposure changes all of it, while a patch
    # on the face would move with the detector's jitter
    bright = float(gray[::8, ::8].mean())
    bx, by, bw, bh = face.box
    head = (float(bx + bw / 2), float(by + bh / 2), float(nx))
    return float(np.mean(contrast)), float(np.mean(dark)), d, head, bright


class BlinkDetector:
    WINDOW = 15        # frames of recent history for the open level
    MAX_CLOSED = 20    # frames (~0.65 s); longer is eyes kept shut, not a blink
    MOTION = 0.15      # head movement, as a fraction of the eye distance

    def __init__(self, drop=DEFAULT_DROP, rise=DEFAULT_RISE):
        self.drop = drop
        self.rise = rise
        self.reset()

    def reset(self):
        self.levels = []      # (contrast, darkness) of recent open frames
        self.closed = 0
        self.noses = []       # recent head positions
        self.moving = 0       # consecutive frames with the head moving
        self.prev_bright = None
        self.stale = False    # wait for clearly open eyes before the next dip
        self.blinked = False
        self.samples = []     # (contrast ratio, darkness rise) per steady frame
        self.last = {}        # what the last frame looked like, for traces
        self.peak = 0.0       # strongest eye-closure score seen (2.0 = closed)

    def update(self, gray, face):
        """Feed one grayscale frame with a face; True once a blink was seen."""
        if self.blinked:
            return True
        feats = eye_features(gray, face)
        return False if feats is None else self.feed(*feats)

    def feed(self, contrast, dark, d, head, bright):
        if self.blinked:
            return True
        # head movement: where the head is now against where it was a third
        # of a second ago, both as medians of 5 frames, so the detector's
        # frame-to-frame jitter doesn't count
        self.noses = (self.noses + [head])[-15:]
        moved = False
        if len(self.noses) >= 12:
            now = np.median(self.noses[-5:], axis=0)
            before = np.median(self.noses[:5], axis=0)
            moved = float(np.abs(now - before).max()) > self.MOTION * d
        changing = self.prev_bright is not None and abs(bright - self.prev_bright) > 0.04 * self.prev_bright
        self.prev_bright = bright
        self.last = {"moved": bool(moved), "changing": bool(changing)}
        if changing:
            # auto-exposure still adjusting (camera start, screen lighting
            # up): start the baseline over
            self.levels, self.closed, self.stale = [], 0, False
            return False
        if len(self.levels) < 5:
            self.levels.append((contrast, dark))
            return False
        recent = np.asarray(self.levels[-self.WINDOW:])
        ratio = contrast / (float(np.median(recent[:, 0])) + 1e-6)
        rise = dark - float(np.median(recent[:, 1]))
        self.last.update(ratio=round(ratio, 3), rise=round(rise, 1))
        if moved:
            # one odd frame (a blink can nudge the face box) is skipped; a
            # head that keeps moving voids the dip in progress
            self.moving += 1
            if self.moving >= 3:
                self.closed, self.stale = 0, True
            return False
        self.moving = 0
        self.samples.append((round(ratio, 3), round(rise, 1)))
        # the contrast drop and the lighter pixels peak a frame apart, so
        # they're added up: each scores 1 at its calibrated threshold
        closure = (1 - ratio) / (1 - self.drop) + max(rise, 0.0) / self.rise
        self.last["closure"] = round(closure, 2)
        self.peak = max(self.peak, closure)
        if closure >= 2.0:
            if not self.stale:
                self.closed += 1
                if self.closed > self.MAX_CLOSED:  # eyes kept shut: not a blink
                    self.closed, self.stale = 0, True
            return False
        if closure < 1.2:
            if self.closed and not self.stale:
                self.blinked = True
                return True
            self.stale = False
            self.closed = 0
        if not self.closed:
            self.levels.append((contrast, dark))
            self.levels = self.levels[-self.WINDOW * 2:]
        return False


def calibrate(open_samples, blink_samples):
    """Thresholds from a stretch with eyes open and still, checked against a
    stretch with blinks. Returns (drop, rise), or None if blinks can't be told
    apart from open eyes."""
    if len(open_samples) < 20 or len(blink_samples) < 20:
        return None
    o = np.asarray(open_samples)
    b = np.asarray(blink_samples)
    drop = round(min(DEFAULT_DROP + 0.02, float(np.percentile(o[:, 0], 1)) - 0.03), 3)
    rise = round(max(DEFAULT_RISE, 1.6 * float(np.percentile(o[:, 1], 99))), 1)
    closed = (1 - b[:, 0]) / (1 - drop) + np.maximum(b[:, 1], 0) / rise >= 2.0
    # count separate dips: at least two blinks must stand out
    dips = int(np.sum(closed[1:] & ~closed[:-1]) + closed[0])
    if dips < 2:
        return None
    return drop, rise
