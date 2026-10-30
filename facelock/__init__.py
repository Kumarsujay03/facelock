"""Face unlock for the GNOME login and lock screens."""
import os

try:
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")) as _f:
        __version__ = _f.read().strip()
except OSError:
    __version__ = "unknown"
