"""Shared helpers for fresh-eyes scripts. Python 3.9 stdlib only."""
import json
import os
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
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


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


def write_png(path, w, h, rgb):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    run(FFMPEG + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (w, h), "-i", "-",
                  "-frames:v", "1", path], input=bytes(rgb))
    return path


def cli(fn):
    """Run a CLI main; turn FEError into {"error": ...} on stdout and exit code 2."""
    try:
        code = fn()
    except FEError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(2)
    sys.exit(code or 0)
