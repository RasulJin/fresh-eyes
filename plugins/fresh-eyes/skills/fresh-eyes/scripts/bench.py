#!/usr/bin/env python3
"""Score fresh-eyes runs against known bugs: did the skill catch them, and how much noise did it make.

cases.json: [{"id", "screen", "platform", "ref", "kind": "bug"|"control", "group": "live"|"october"|..., "pair": sha|null,
              "expect": {"checks": [...], "keywords": [...], "bbox": [x,y,w,h]|null}}]
A case is caught when a confirmed/unsure finding on its screen and platform names an expected check or keyword
(and overlaps the expected bbox, if one is given) in at least 2/3 of the attempts.

CLI: bench.py score --cases cases.json --runs RUN [RUN ...] [--fix-runs ID=RUN ...] [--out bench.json]
"""
import argparse
import glob
import json
import os

from fe_common import cli, read_json, write_json

COUNTED = ("confirmed", "unsure")


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / float(union) if union else 0.0


def match(case, findings):
    exp = case.get("expect") or {}
    checks = set(exp.get("checks") or [])
    words = [w.casefold() for w in exp.get("keywords") or []]
    for fd in findings:
        if fd.get("screen") != case["screen"] or fd.get("platform") != case["platform"]:
            continue
        if fd.get("verdict") not in COUNTED:
            continue
        text = (fd.get("title", "") + " " + fd.get("actual", "")).casefold()
        if not (fd.get("check") in checks or any(w in text for w in words)):
            continue
        if exp.get("bbox") and fd.get("bbox") and _iou(exp["bbox"], fd["bbox"]) < 0.1:
            continue
        return True
    return False


def score(cases, attempts):
    out = {}
    for c in cases:
        if c.get("kind") == "control":
            continue
        hits = sum(1 for a in attempts if match(c, a.get(c["id"], [])))
        out[c["id"]] = {"hits": hits, "attempts": len(attempts), "caught": hits * 3 >= 2 * len(attempts) > 0}
    return out


def false_positives(cases, fix_runs, control_runs):
    pairs = sum(1 for c in cases if c["id"] in fix_runs and match(c, fix_runs[c["id"]]))
    control = 0
    for c in cases:
        if c.get("kind") == "control":
            control += sum(1 for fd in control_runs.get(c["id"], [])
                           if fd.get("screen") == c["screen"] and fd.get("verdict") == "confirmed")
    return {"pairs": pairs, "control": control}


def summary(cases, scores, fps, confirmed=0, rejected=0):
    groups = {}
    for c in cases:
        if c["id"] in scores:
            g = groups.setdefault(c.get("group", "other"), [0, 0])
            g[0] += 1 if scores[c["id"]]["caught"] else 0
            g[1] += 1
    res = {g: "%d/%d" % tuple(v) for g, v in groups.items()}
    res.update(false_positives=fps, rejected_share=round(rejected / float(confirmed + rejected), 3)
               if confirmed + rejected else None)
    return res


def load_run_findings(run, cases):
    """Map case id -> findings of the case's screen/platform in one run folder."""
    by_screen = {}
    for p in glob.glob(os.path.join(run, "*", "*", "findings.json")):
        platform, screen = p.split(os.sep)[-3], p.split(os.sep)[-2]
        by_screen[(platform, screen)] = read_json(p).get("findings", [])
    return {c["id"]: by_screen.get((c["platform"], c["screen"]), []) for c in cases}


def _verdict_counts(runs):
    confirmed = rejected = 0
    for run in runs:
        for p in glob.glob(os.path.join(run, "*", "*", "findings.json")):
            for fd in read_json(p).get("findings", []):
                confirmed += fd.get("verdict") == "confirmed"
                rejected += fd.get("verdict") == "rejected"
    return confirmed, rejected


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("score")
    p.add_argument("--cases", required=True)
    p.add_argument("--runs", nargs="+", required=True)
    p.add_argument("--fix-runs", nargs="*", default=[], help="CASE_ID=RUN on the fix commit")
    p.add_argument("--out")
    a = ap.parse_args()
    cases = read_json(a.cases)
    attempts = [load_run_findings(r, cases) for r in a.runs]
    fix = {}
    for item in a.fix_runs:
        cid, run = item.split("=", 1)
        fix[cid] = load_run_findings(run, cases).get(cid, [])
    scores = score(cases, attempts)
    fps = false_positives(cases, fix, {c["id"]: attempts[0].get(c["id"], []) for c in cases})
    res = {"cases": scores, "summary": summary(cases, scores, fps, *_verdict_counts(a.runs))}
    if a.out:
        write_json(a.out, res)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    cli(main)
