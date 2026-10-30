"""Updates from GitHub releases.

Each release carries facelock-<version>.tar.gz and its .sha256. An update
downloads both over HTTPS, checks the checksum, unpacks the tarball and runs
its install.sh --update, which keeps your settings, face data and keyring
choice.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

from . import __version__

REPO = "Kumarsujay03/facelock"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
STATE = "/var/lib/facelock/update-available"


def _version_tuple(v):
    parts = []
    for p in v.lstrip("v").split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": f"facelock/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest():
    """(version, tarball url, sha256 url, release page) of the newest release."""
    rel = json.loads(_get(API))
    version = rel["tag_name"].lstrip("v")
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    tar = f"facelock-{version}.tar.gz"
    if tar not in assets or tar + ".sha256" not in assets:
        raise RuntimeError(f"release {version} has no {tar} with a checksum")
    return version, assets[tar], assets[tar + ".sha256"], rel.get("html_url", "")


def newer_than_installed(version):
    return _version_tuple(version) > _version_tuple(__version__)


def remember(version):
    """Note an available update for `facelock status` (or clear the note)."""
    try:
        if version:
            with open(STATE, "w") as f:
                f.write(version + "\n")
        elif os.path.exists(STATE):
            os.unlink(STATE)
    except OSError:
        pass


def available():
    try:
        with open(STATE) as f:
            v = f.read().strip()
        return v if newer_than_installed(v) else None
    except OSError:
        return None


def install(version, tar_url, sum_url, say=print):
    with tempfile.TemporaryDirectory(prefix="facelock-update-") as tmp:
        path = os.path.join(tmp, "facelock.tar.gz")
        say(f"Downloading facelock {version}...")
        data = _get(tar_url, timeout=120)
        expected = _get(sum_url).decode().split()[0].strip().lower()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise RuntimeError("the download doesn't match its checksum; nothing was changed")
        with open(path, "wb") as f:
            f.write(data)
        with tarfile.open(path) as t:
            t.extractall(tmp, filter="data")
        src = os.path.join(tmp, f"facelock-{version}")
        if not os.path.isfile(os.path.join(src, "install.sh")):
            raise RuntimeError("the release archive has no install.sh")
        say("Installing...")
        subprocess.run(["sh", os.path.join(src, "install.sh"), "--update"], check=True)
        shutil.rmtree(src, ignore_errors=True)
    remember(None)
