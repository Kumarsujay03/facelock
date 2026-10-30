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
    # [match]  (SFace cosine similarity, 0-1)
    # A look-alike relative scored up to 0.54 against an enrolled face in real
    # use; its owner usually 0.60-0.88. The bars below keep a margin.
    accept: float = 0.58          # a frame this similar to you counts...
    accept_frames: int = 3        # ...and this many such frames unlock
    accept_instant: float = 0.66  # or one frame this similar to your enrolled face
    learned_floor: float = 0.50   # learned samples only count for a frame this close to the enrolment
    learn_from: float = 0.68      # only frames this close to the enrolment are learned
    max_learned: int = 40
    other_margin: float = 0.03    # a face this close to an "other" (facelock add-other) is refused
    # [unlock]
    scan_seconds: float = 8.0     # longest a scan keeps the camera on
    attempt_seconds: float = 1.5  # a face that doesn't match for this long = one failed attempt
    max_failures: int = 3         # then face unlock pauses until you unlock another way
    min_face_px: int = 90         # ignore faces smaller than this (too far away)
    # [liveness]
    liveness: str = "observe"     # "blink" (required), "observe" (logged only) or "off"
    # [lowlight]
    dark_luma: float = 55.0       # mean brightness (0-255) below which the screen lights up
    light_after: float = 1.5      # seconds of trying without the light first
    lowlight: bool = True
    # [update]
    auto_update: bool = False

    SECTIONS = {
        "camera": ["device"],
        # older keys (threshold, frames_needed, instant_threshold,
        # learn_threshold, relearn_*) are ignored: they allowed too weak a match
        "match": ["accept", "accept_frames", "accept_instant", "learned_floor", "learn_from",
                  "max_learned", "other_margin"],
        "unlock": ["scan_seconds", "attempt_seconds", "max_failures", "min_face_px"],
        "liveness": ["liveness"],
        "lowlight": ["dark_luma", "lowlight", "light_after"],
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
