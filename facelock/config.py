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
    # [match]  similarities 0-1. Two models must both recognise the face,
    # over strong_frames frames in a row.
    # AuraFace (the deciding model): on the LFW benchmark no stranger's
    # 5-photo median passed 0.34; enrolment raises the bar for faces that
    # match themselves closely.
    strong_accept: float = 0.62   # lowest AuraFace median bar (scan.STRONG_FLOOR is the hard floor)
    strong_frames: int = 5        # frames that must all agree
    strong_spread: float = 0.10   # no frame more than this below the bar
    strong_same_person: float = 0.45  # every pair of those frames is one person
    strong_learned_floor: float = 0.45
    learn_strong: float = 0.75    # only frames this close (AuraFace) are learned...
    # SFace (the first check): a look-alike brother reached 0.54 with it
    accept: float = 0.58          # median bar over the same frames
    sface_gate: float = 0.45      # frames below this aren't worth AuraFace's time
    learned_floor: float = 0.50   # learned samples only count for a frame this close to the enrolment
    learn_from: float = 0.68      # ...and this close (SFace) to the enrolment
    max_learned: int = 40
    other_margin: float = 0.03    # a face this close to an "other" (facelock add-other) is refused
    # [unlock]
    scan_seconds: float = 8.0     # longest a scan keeps the camera on
    attempt_seconds: float = 2.5  # a face that doesn't match for this long = one failed attempt
    max_failures: int = 3         # failed tries before face unlock pauses...
    pause_seconds: float = 30.0   # ...for this long the first time; after 3 more, until you unlock another way
    min_face_px: int = 90         # ignore faces smaller than this (too far away)
    # [liveness]
    liveness: str = "observe"     # "blink" (required), "observe" (logged only) or "off"
    antispoof: str = "require"    # photo/screen check: "require", "observe" (logged only) or "off"
    spoof_live: float = 0.50      # median live-face probability the deciding frames need
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
        "match": ["strong_accept", "strong_frames", "strong_spread", "strong_same_person",
                  "strong_learned_floor", "learn_strong", "accept", "sface_gate", "learned_floor",
                  "learn_from", "max_learned", "other_margin"],
        "unlock": ["scan_seconds", "attempt_seconds", "max_failures", "pause_seconds", "min_face_px"],
        "liveness": ["liveness", "antispoof", "spoof_live"],
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
    if cfg.antispoof not in ("require", "observe", "off"):
        cfg.antispoof = "require"
    # never weaker than a coin toss; a typo can't switch the check off
    cfg.spoof_live = min(max(cfg.spoof_live, 0.30), 0.95)
    return cfg
