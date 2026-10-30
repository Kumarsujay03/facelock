"""Face detection (YuNet) and two independent face recognition models, all
ONNX run by OpenCV on the CPU, so the same code works on x86 and ARM laptops:

- SFace (128 numbers per face): small and fast, the first check.
- AuraFace (512 numbers per face, ResNet100 trained with ArcFace's angular
  margin loss): much better at telling similar faces apart, the deciding check.

Both describe the face's features (the shape and layout of eyes, nose, mouth,
jaw...) after aligning it by its landmarks, not its pixels; a face unlocks
only if both recognise it."""
import os

import cv2
import numpy as np

from .config import MODEL_DIR

AURAFACE = "auraface_glintr100.onnx"
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
        self.strong = cv2.dnn.readNetFromONNX(os.path.join(model_dir, AURAFACE))
        # the first run is slow (about 0.6 s); do it now, not during an unlock
        self.strong.setInput(self._blob(np.zeros((112, 112, 3), np.uint8)))
        self.strong.forward()
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

    def align(self, frame, face):
        """The face cut out and turned upright by its 5 landmarks, 112x112
        (the standard ArcFace alignment both models expect)."""
        return self.recognizer.alignCrop(frame, face.raw)

    def embed(self, frame, face, crop=None):
        """SFace: L2-normalised 128-d features of the aligned face."""
        crop = self.align(frame, face) if crop is None else crop
        vec = self.recognizer.feature(crop).reshape(-1).astype(np.float32)
        return vec / (np.linalg.norm(vec) + 1e-9)

    @staticmethod
    def _blob(crop):
        # RGB, scaled to -1..1, as AuraFace (insightface ArcFace) was trained
        return cv2.dnn.blobFromImage(crop, 1 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)

    def embed_strong(self, frame, face, crop=None):
        """AuraFace: L2-normalised 512-d features of the aligned face."""
        crop = self.align(frame, face) if crop is None else crop
        self.strong.setInput(self._blob(crop))
        vec = self.strong.forward().reshape(-1).astype(np.float32)
        return vec / (np.linalg.norm(vec) + 1e-9)


def top_similarity(vec, gallery, k=None):
    """Mean of the k best cosine similarities: a few lucky samples aren't
    enough. k grows with the gallery (3 for up to 23 samples, then one per 6
    samples): the more samples there are, the easier it is for someone else
    to be close to a few of them by chance."""
    if gallery is None or not len(gallery):
        return -1.0
    k = max(3, len(gallery) // 6) if k is None else k
    sims = np.sort(gallery @ vec)[::-1]
    return float(sims[:k].mean())


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
