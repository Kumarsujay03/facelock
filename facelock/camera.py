"""Finding and reading the built-in camera.

Works with plain USB (UVC) webcams and with cameras that need a userspace
pipeline (Intel IPU6/IPU7 behind v4l2-relayd, libcamera's v4l2 relay), as
long as they show up as a V4L2 capture device that delivers frames.
"""
import fcntl
import glob
import os
import struct
import time

import cv2

VIDIOC_QUERYCAP = 0x80685600
CAP_VIDEO_CAPTURE = 0x00000001
CAP_DEVICE_CAPS = 0x80000000

# raw sensor/ISP nodes that only work inside a vendor pipeline
SKIP_DRIVERS = ("isys", "ipu6", "ipu7", "intel-ipu", "qcom-camss", "camss", "rkisp", "unicam")


def _querycap(path):
    buf = bytearray(104)
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, VIDIOC_QUERYCAP, buf)
    finally:
        os.close(fd)
    driver, card, bus = (bytes(buf[o:o + n]).split(b"\0")[0].decode(errors="replace")
                         for o, n in ((0, 16), (16, 32), (48, 32)))
    caps, device_caps = struct.unpack_from("II", buf, 84)
    if caps & CAP_DEVICE_CAPS:
        caps = device_caps
    return driver, card, bus, caps


def candidates():
    """Capture devices, best first: a relayed/loopback camera, then UVC, then the rest.
    Infrared cameras are left out (they need their own pipeline)."""
    found = []
    for path in sorted(glob.glob("/dev/video*"), key=lambda p: int(p[10:] or 0)):
        try:
            driver, card, bus, caps = _querycap(path)
        except OSError:
            continue
        if not caps & CAP_VIDEO_CAPTURE:
            continue
        low = f"{driver} {card}".lower()
        if any(s in low for s in SKIP_DRIVERS) or " ir " in f" {low} " or "infrared" in low:
            continue
        rank = 0 if driver == "v4l2 loopback" else 1 if driver == "uvcvideo" else 2
        found.append((rank, path, card))
    return [(p, c) for _, p, c in sorted(found)]


class Camera:
    def __init__(self, device="auto", width=1280, height=720):
        self.device = device
        self.width, self.height = width, height
        self.cap = None
        self.path = None

    def open(self):
        paths = [self.device] if self.device != "auto" else [p for p, _ in candidates()]
        if not paths:
            raise RuntimeError("no camera found")
        last = None
        for path in paths:
            cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
            if not cap.isOpened():
                last = f"cannot open {path}"
                continue
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
            self.cap, self.path = cap, path
            return self
        raise RuntimeError(last or "no camera could be opened")

    def frames(self, read_timeout=3.0):
        """Yield (seconds since open, BGR frame) until the caller stops."""
        t0 = time.monotonic()
        last_ok = t0
        while True:
            ok, frame = self.cap.read()
            now = time.monotonic()
            if not ok or frame is None:
                if now - last_ok > read_timeout:
                    raise RuntimeError(f"camera {self.path} stopped delivering frames")
                time.sleep(0.01)
                continue
            last_ok = now
            yield now - t0, frame

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
