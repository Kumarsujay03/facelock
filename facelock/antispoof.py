"""Photo and screen check: is the face in front of the camera a live face, or a
printed photo or a phone or monitor showing one?

The face models only say whose face it is, and a good photo of you is still
your face. This uses Minivision's Silent-Face-Anti-Spoofing models
(MiniFASNetV2 and MiniFASNetV1SE, Apache-2.0), the passive check RGB-only
phones use: from a single frame they look for what a reproduction loses or
adds (print texture, screen moire and glare, the edge of the phone or paper,
flat lighting). Each model sees the face with a different amount of
surroundings (2.7x and 4x the face box), and their outputs are averaged as
upstream does: class 1 is a live face, 0 and 2 are the two kinds of
reproduction it was trained on.

It needs nothing from the user and runs on the same frames as the face check.
It is not as strong as an IR camera: score each frame, and judge the median
over the window rather than any one frame.
"""
import os

import cv2
import numpy as np

MODELS = (("antispoof_minifasnet_v2.onnx", 2.7), ("antispoof_minifasnet_v1se.onnx", 4.0))
SIZE = 80


def crop(frame, face, scale):
    """The face box grown by scale around its centre (less near the frame
    edges), resized to 80x80; the same crop the models were trained on."""
    src_h, src_w = frame.shape[:2]
    x, y, w, h = (float(v) for v in face.box[:4])
    if w <= 0 or h <= 0:
        return None
    scale = min((src_h - 1) / h, (src_w - 1) / w, scale)
    cx, cy = x + w / 2, y + h / 2
    x1 = max(0, int(cx - w * scale / 2))
    y1 = max(0, int(cy - h * scale / 2))
    x2 = min(src_w - 1, int(cx + w * scale / 2))
    y2 = min(src_h - 1, int(cy + h * scale / 2))
    if x2 <= x1 or y2 <= y1:
        return None
    return cv2.resize(frame[y1:y2 + 1, x1:x2 + 1], (SIZE, SIZE))


class AntiSpoof:
    def __init__(self, model_dir):
        self.nets = []
        for name, scale in MODELS:
            path = os.path.join(model_dir, name)
            self.nets.append((cv2.dnn.readNetFromONNX(path), scale))

    @staticmethod
    def available(model_dir):
        return all(os.path.exists(os.path.join(model_dir, n)) for n, _ in MODELS)

    def live(self, frame, face):
        """Probability (0-1) that this is a live face, or None if the face is
        too close to the edge to crop."""
        probs = np.zeros(3)
        for net, scale in self.nets:
            img = crop(frame, face, scale)
            if img is None:
                return None
            # BGR, 0-255, no normalisation: as the models were trained
            net.setInput(img.astype(np.float32).transpose(2, 0, 1)[None])
            logits = net.forward()[0].astype(np.float64)
            e = np.exp(logits - logits.max())
            probs += e / e.sum()
        return float(probs[1] / len(self.nets))
