#!/usr/bin/env python3
"""Spec slices for reviewers and verifiers, and the screens whose code changed since the spec was checked.

CLI:
  spec.py slice --spec F --screens a,b --out OUT [--stale J]
  spec.py stale --spec F --repo R --config C --out OUT
"""
import argparse
import json
import os
import re
import time

from coverage import screens_from_diff
from fe_common import FEError, cli, read_json, run, write_json

GLOBAL = "Global"  # the section every screen follows (spec-format.md)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def sections(text):
    """(header, [(id, text)]): a section runs from a `## <id> ...` line to the next one."""
    parts = re.split(r"(?m)^(?=## )", text)
    return parts[0], [((p[3:].split() or [""])[0], p) for p in parts[1:]]


def slice_spec(text, screens, stale=None):
    """The header, Global and the listed screens' sections in spec order. A screen without a section gets one
    line saying so; a stale one (spec-stale.json) a warning under its heading."""
    header, secs = sections(text)
    old = set((stale or {}).get("stale_screens", []))
    out = header
    for sid, body in secs:
        if sid == GLOBAL or sid in screens:
            if sid in old:
                head, _, rest = body.partition("\n")
                body = "%s\n⚠ stale since %s\n%s" % (head, stale.get("updated", "?"), rest)
            out += body
    found = {sid for sid, _ in secs}
    missing = ["## %s: no section in spec.md\n" % s for s in screens if s not in found]
    return out.rstrip("\n") + "\n\n" + "\n".join(missing) if missing else out


def stale(text, repo, config):
    """Screens whose code changed after the spec's `Updated:` day (git log base, then coverage's diff mapping).
    A spec dated today was checked against today's code: nothing is stale."""
    m = re.search(r"^Updated:\s*(\d{4}-\d{2}-\d{2})", text, re.M)
    if not m:
        raise FEError("the spec has no `Updated: YYYY-MM-DD` line")
    updated = m.group(1)
    base = run(["git", "-C", repo, "log", "-1", "--until=%sT23:59:59" % updated, "--format=%H"]).stdout.decode().strip()
    if not base:
        raise FEError("no commit on or before %s in %s" % (updated, repo))
    screens = [] if updated >= time.strftime("%Y-%m-%d") else screens_from_diff(repo, base, config)["screens"]
    return {"updated": updated, "base": base, "stale_screens": screens}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("slice", help="the spec part a screen's reviewer and verifier read")
    p.add_argument("--spec", required=True); p.add_argument("--screens", required=True, help="comma list of ids")
    p.add_argument("--out", required=True); p.add_argument("--stale", help="spec-stale.json written by stale")
    p = sub.add_parser("stale", help="screens whose code changed since the spec's Updated date")
    p.add_argument("--spec", required=True); p.add_argument("--repo", required=True)
    p.add_argument("--config", required=True); p.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "slice":
        screens = [s.strip() for s in a.screens.split(",") if s.strip()]
        text = slice_spec(_read(a.spec), screens, read_json(a.stale) if a.stale else None)
        if os.path.dirname(a.out):
            os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        out = {"out": a.out, "screens": screens}
    else:
        out = stale(_read(a.spec), a.repo, read_json(a.config))
        write_json(a.out, out)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    cli(main)
