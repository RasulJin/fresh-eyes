"""Shared helpers for fresh-eyes scripts. Python 3.9 stdlib only."""
import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys


class FEError(Exception):
    """An expected failure with a message meant for the agent reading the output."""


def run(cmd, timeout=60, input=None, check=True, redact=()):
    """Run a command given as a list; return CompletedProcess with bytes stdout/stderr.
    `redact`: strings (typed secrets) that must never appear in an error message."""
    def shown(text):
        for secret in redact:
            if secret:
                text = text.replace(secret, "***")
        return text
    try:
        proc = subprocess.run(cmd, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)
    except FileNotFoundError:
        raise FEError("command not found: %s" % cmd[0])
    except subprocess.TimeoutExpired:
        raise FEError(shown("timed out after %ss: %s" % (timeout, " ".join(cmd))))
    if check and proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip()[-400:]
        raise FEError(shown("exit %d: %s\n%s" % (proc.returncode, " ".join(cmd), tail)))
    return proc


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    """Atomic: readers see the old file or the new one, never half of it."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def run_dir(path):
    """The run folder: the first one with coverage.json at or above `path`; None outside a run."""
    d = os.path.abspath(path)
    while not os.path.exists(os.path.join(d, "coverage.json")):
        if os.path.dirname(d) == d:
            return None
        d = os.path.dirname(d)
    return d


def beat_file(queue=None):
    """The heartbeat's name: one per queue when two navigators share a platform (coverage.py plan queues)."""
    return "heartbeat-%s" % queue if queue else "heartbeat"


def queue_name(value):
    """argparse type of --queue: a plain word, it names a file."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value or ""):
        raise argparse.ArgumentTypeError("--queue takes a plain word such as main or small")
    return value


def heartbeat(path, platform, did, queue=None):
    """Refresh <run>/<platform>/heartbeat (and heartbeat-<queue> with a queue), <run> being the folder with
    coverage.json at or above `path` (nav.py --run, capture.py --out): the orchestrator stops a navigator only
    when it is 10 minutes old. Never fails the command it marks."""
    d = run_dir(path)
    if d is None:
        return
    try:
        for name in {beat_file(), beat_file(queue)}:
            write_json(os.path.join(d, platform, name), {"did": did})
    except OSError:
        pass


@contextlib.contextmanager
def locked(path):
    """Exclusive lock for a read-modify-write of `path` (parallel navigators update one coverage.json)."""
    with open(path + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


FFMPEG = ["ffmpeg", "-y", "-loglevel", "error"]


def image_size(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
               "-of", "csv=p=0:s=x", path]).stdout.decode().strip()
    w, h = out.splitlines()[0].split("x")
    return int(w), int(h)


def read_rgb(path):
    """Decode any image ffmpeg reads into (w, h, rgb24 bytes)."""
    w, h = image_size(path)
    data = run(FFMPEG + ["-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-frames:v", "1", "-"]).stdout
    if len(data) != w * h * 3:
        raise FEError("decoded %d bytes, expected %d for %s" % (len(data), w * h * 3, path))
    return w, h, data


def read_gray(path, w, h, flags=None):
    """Decode an image scaled to w x h into gray bytes (flags: an ffmpeg scaler such as "area")."""
    vf = "scale=%d:%d%s,format=gray" % (w, h, ":flags=" + flags if flags else "")
    return run(FFMPEG + ["-i", path, "-vf", vf, "-f", "rawvideo", "-frames:v", "1", "-"]).stdout


def write_png(path, w, h, rgb):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    run(FFMPEG + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (w, h), "-i", "-",
                  "-frames:v", "1", path], input=bytes(rgb))
    return path


HIDDEN = "‹secret›"
_secrets = set()  # values never printed or written: config accounts' credentials and what nav.py typed
_configs = set()  # config files already read
_searched = set()


def secret_values(config):
    """Test credentials of config `accounts`: each set `*_env` variable, else the keychain item
    fresh-eyes-<role>-<field> (what nav.py type would type). A read that fails gives nothing."""
    found = []
    for role, acc in sorted((config.get("accounts") or {}).items()):
        for key, var in sorted((acc or {}).items()):
            if not key.endswith("_env"):
                continue
            value = os.environ.get(var or "")
            if not value:
                try:
                    proc = run(["security", "find-generic-password", "-s", "fresh-eyes-%s-%s" % (role, key[:-4]),
                                "-w"], timeout=10, check=False)
                    value = proc.stdout.decode("utf-8", "replace").rstrip("\n") if proc.returncode == 0 else ""
                except FEError:  # no `security`: not a Mac
                    value = ""
            if value:
                found.append(value)
    return found


def config_path(start):
    """The project's .fresh-eyes/config.json at or above `start` (a run folder lies under .fresh-eyes/runs), or
    None."""
    d = os.path.abspath(start)
    while not os.path.isfile(os.path.join(d, ".fresh-eyes", "config.json")):
        if os.path.dirname(d) == d:
            return None
        d = os.path.dirname(d)
    return os.path.join(d, ".fresh-eyes", "config.json")


def load_secrets(start):
    """Read the credentials of the .fresh-eyes/config.json at or above `start` (once per config)."""
    d = os.path.abspath(start)
    if d in _searched:
        return
    _searched.add(d)
    path = config_path(d)
    if path is None or path in _configs:
        return
    _configs.add(path)
    try:
        _secrets.update(secret_values(read_json(path)))
    except (OSError, ValueError):
        pass


def add_secret(value):
    if value:
        _secrets.add(value)


def redact(text):
    """`text` without any credential (case-insensitive: an app may lowercase an email)."""
    load_secrets(os.getcwd())
    for s in sorted(_secrets, key=len, reverse=True):
        text = re.sub(re.escape(s), HIDDEN, text, flags=re.I)
    return text


def scrub(obj):
    """redact() on every string of a JSON-like object."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    return obj


def cli(fn):
    """Run a CLI main; turn FEError into {"error": ...} on stdout and exit code 2."""
    try:
        code = fn()
    except FEError as e:
        print(json.dumps({"error": redact(str(e))}, ensure_ascii=False))
        sys.exit(2)
    sys.exit(code or 0)
