"""Settings from /etc/facelock/facelock.conf (INI), with safe defaults."""
import configparser
import os
from dataclasses import dataclass, fields

CONFIG_PATH = os.environ.get("FACELOCK_CONFIG", "/etc/facelock/facelock.conf")
STATE_DIR = os.environ.get("FACELOCK_STATE", "/var/lib/facelock")
SOCKET_PATH = os.environ.get("FACELOCK_SOCKET", "/run/facelock/socket")
MODEL_DIR = os.environ.get(
    "FACELOCK_MODELS",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models"),
)


@dataclass
class Config:
    # [camera]
    device: str = "auto"          # "auto" or a path such as /dev/video0
    # [match]
    threshold: float = 0.45       # SFace cosine similarity needed on a frame
    frames_needed: int = 2        # frames at or above threshold in one attempt
    instant_threshold: float = 0.62  # one frame this good is enough
    learn_threshold: float = 0.58    # only learn from unlocks this certain
    max_learned: int = 40
    # a scan that saw a likely-you face but didn't unlock is learned too, if
    # you then unlock with fingerprint or password within relearn_seconds
    relearn_min: float = 0.38
    relearn_seconds: float = 60.0
    # [unlock]
    scan_seconds: float = 8.0     # longest a scan keeps the camera on
    attempt_seconds: float = 1.5  # a face that doesn't match for this long = one failed attempt
    max_failures: int = 3         # then face unlock pauses until you unlock another way
    min_face_px: int = 90         # ignore faces smaller than this (too far away)
    # [liveness]
    liveness: str = "observe"     # "blink" (required), "observe" (logged only) or "off"
    # [lowlight]
    dark_luma: float = 55.0       # mean brightness (0-255) below which the screen lights up
    lowlight: bool = True
    # [update]
    auto_update: bool = False

    SECTIONS = {
        "camera": ["device"],
        "match": ["threshold", "frames_needed", "instant_threshold", "learn_threshold", "max_learned",
                  "relearn_min", "relearn_seconds"],
        "unlock": ["scan_seconds", "attempt_seconds", "max_failures", "min_face_px"],
        "liveness": ["liveness"],
        "lowlight": ["dark_luma", "lowlight"],
        "update": ["auto"],
    }


def load(path=CONFIG_PATH):
    cfg = Config()
    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    try:
        parser.read(path)
    except configparser.Error:
        return cfg
    types = {f.name: f.type for f in fields(Config)}
    types["auto"] = bool
    for section, keys in Config.SECTIONS.items():
        for key in keys:
            if not parser.has_option(section, key):
                continue
            kind = types[key]
            try:
                if kind in (bool, "bool"):
                    value = parser.getboolean(section, key)
                elif kind in (int, "int"):
                    value = parser.getint(section, key)
                elif kind in (float, "float"):
                    value = parser.getfloat(section, key)
                else:
                    value = parser.get(section, key).strip()
            except ValueError:
                continue
            setattr(cfg, "auto_update" if key == "auto" else key, value)
    if cfg.liveness not in ("blink", "observe", "off"):
        cfg.liveness = "observe"
    return cfg
