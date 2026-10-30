"""Face detection (YuNet) and recognition (SFace), both ONNX models run by
OpenCV on the CPU, so the same code works on x86 and ARM laptops."""
import os

import cv2
import numpy as np

from .config import MODEL_DIR

DETECT_W = 640  # detection runs on a downscaled copy; landmarks are scaled back


class Face:
    __slots__ = ("box", "landmarks", "score", "raw")

    def __init__(self, row, scale):
        self.raw = row.copy()
        self.raw[:14] *= scale
        self.box = self.raw[:4]            # x, y, w, h
        self.landmarks = self.raw[4:14].reshape(5, 2)  # r-eye, l-eye, nose, r-mouth, l-mouth
        self.score = float(row[14])

    @property
    def size(self):
        return float(min(self.box[2], self.box[3]))


class Engine:
    def __init__(self, model_dir=MODEL_DIR):
        self.detector = cv2.FaceDetectorYN.create(
            os.path.join(model_dir, "face_detection_yunet_2023mar.onnx"), "", (DETECT_W, 360), 0.75, 0.3, 20)
        self.recognizer = cv2.FaceRecognizerSF.create(
            os.path.join(model_dir, "face_recognition_sface_2021dec.onnx"), "")
        self._size = None

    def detect(self, frame):
        """Largest face in the frame, or None."""
        h, w = frame.shape[:2]
        scale = w / DETECT_W
        size = (DETECT_W, int(round(h / scale)))
        if size != self._size:
            self.detector.setInputSize(size)
            self._size = size
        _, faces = self.detector.detect(cv2.resize(frame, size, interpolation=cv2.INTER_AREA))
        if faces is None or not len(faces):
            return None
        best = max(faces, key=lambda r: r[2] * r[3])
        return Face(best, scale)

    def embed(self, frame, face):
        """L2-normalised 128-d embedding of an aligned face crop."""
        crop = self.recognizer.alignCrop(frame, face.raw)
        vec = self.recognizer.feature(crop).reshape(-1).astype(np.float32)
        return vec / (np.linalg.norm(vec) + 1e-9)


def similarity(vec, gallery):
    """Best cosine similarity of vec against the rows of gallery."""
    if gallery is None or not len(gallery):
        return -1.0
    return float(np.max(gallery @ vec))


def luma(frame):
    """Mean brightness 0-255 of a frame (sampled, cheap)."""
    return float(frame[::8, ::8].mean())


def sharpness(frame, face):
    x, y, w, h = (int(v) for v in face.box)
    crop = frame[max(y, 0):y + h, max(x, 0):x + w]
    if crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())
