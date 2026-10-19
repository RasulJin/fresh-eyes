"""Shared helpers for fresh-eyes scripts. Python 3.9 stdlib only."""
import json
import os
import subprocess
import sys


class FEError(Exception):
    """An expected failure with a message meant for the agent reading the output."""


def run(cmd, timeout=60, input=None, check=True):
    """Run a command given as a list; return CompletedProcess with bytes stdout/stderr."""
    try:
        proc = subprocess.run(cmd, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)
    except FileNotFoundError:
        raise FEError("command not found: %s" % cmd[0])
    except subprocess.TimeoutExpired:
        raise FEError("timed out after %ss: %s" % (timeout, " ".join(cmd)))
    if check and proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip()[-400:]
        raise FEError("exit %d: %s\n%s" % (proc.returncode, " ".join(cmd), tail))
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


def cli(fn):
    """Run a CLI main; turn FEError into {"error": ...} on stdout and exit code 2."""
    try:
        code = fn()
    except FEError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(2)
    sys.exit(code or 0)
