#!/usr/bin/env python3
"""Score fresh-eyes runs against known bugs the way the spec's benchmark asks: code finds the candidates, a model
judges them.

cases.json: [{"id", "screen", "also_screens", "platform", "kind": "bug"|"control", "group": "live"|"october"|...,
              "expect": {"description": "what a person sees", "bbox": [x,y,w,h]|null}}]
            a "control" case is intended behaviour that must not be flagged; it is checked on every run.
sheet: for every case and run (the attempts on the build with the bug, the run on the build with the fix), the
       confirmed/unsure findings on the case's screen and platform, and area when a bbox is given (the code check).
       Which run is a fix run goes to <sheet>.roles.json, so the judge cannot lean on it.
judge: one `fresh-eyes:judge` sets "match" on every entry: the ref of the finding that is the case's defect, or false.
score: caught = matched in at least 2/3 of the attempts; a match on the fix build or on a control is a false
       positive; a run whose
       code verdict was CLEAN while it missed a case it planned is a false CLEAN (the number that must stay 0).

seed:  a run folder from the seeded fixtures (references/bench: one human-class defect per screen, and a control),
       with a spec slice per screen and every cell done, for reviewer, verifier and judge without a device.

CLI: bench.py sheet --cases cases.json --runs RUN [RUN ...] [--fix-runs ID=RUN ...] --out sheet.json
     bench.py score --cases cases.json --sheet sheet.json [--out bench.json] [--run RUN]
           (--run: the full run it gates; its score.json gets "bench": {"caught": n, "of": cases})
     bench.py seed --out RUN
"""
import argparse
import glob
import json
import os
import shutil

from coverage import STATES
from fe_common import FEError, cli, read_json, write_json
from geometry import _area, _intersect
from motion import DEFAULT_CEILING_MS
from spec import slice_spec

COUNTED = ("confirmed", "unsure")
SEEDED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "references", "bench")


def _iou(a, b):
    inter = _area(_intersect(a, b))
    union = _area(a) + _area(b) - inter
    return inter / float(union) if union else 0.0


def candidates(case, findings):
    exp = case.get("expect") or {}
    screens = {case["screen"]} | set(case.get("also_screens") or [])
    return [fd for fd in findings if fd.get("screen") in screens and fd.get("platform") == case["platform"]
            and fd.get("verdict") in COUNTED
            and not (exp.get("bbox") and fd.get("bbox") and _iou(exp["bbox"], fd["bbox"]) < 0.1)]


def load_run_findings(run, cases):
    """Map case id -> all findings of the case's platform in one run, each tagged with its ref (journey findings
    carry the real screen they concern)."""
    paths = sorted(glob.glob(os.path.join(run, "*", "*", "findings*.json")))
    if not paths:  # a run id instead of its folder gives every case zero candidates, which reads as a miss
        raise FEError("no <platform>/<screen>/findings*.json in %s: pass the folder of a reviewed run "
                      "(.fresh-eyes/runs/<id>)" % run)
    by_platform = {}
    for p in paths:
        for fd in read_json(p).get("findings", []):
            fd = dict(fd, ref="%s#%s" % (os.path.relpath(p, run), fd.get("id")))
            by_platform.setdefault(p.split(os.sep)[-3], []).append(fd)
    return {c["id"]: by_platform.get(c["platform"], []) for c in cases}


def _planned(run):
    """(platform, screen) of the run's coverage cells; None when the run has no coverage.json."""
    path = os.path.join(run, "coverage.json")
    return {(c["platform"], c["screen"]) for c in read_json(path)["cells"]} if os.path.exists(path) else None


def _plans(case, planned):
    return planned is None or any((case["platform"], s) in planned
                                  for s in [case["screen"]] + case.get("also_screens", []))


def sheet(cases, runs, fix_runs):
    """(sheet for the judge, roles kept apart from it)."""
    entries, roles = [], {}
    planned = {r: _planned(r) for r in runs}
    # a run of another group never planned the case's screen: not an attempt at it, so not a miss
    todo = [(c, r, "attempt") for c in cases if c.get("kind", "bug") == "bug" for r in runs if _plans(c, planned[r])]
    todo += [(c, fix_runs[c["id"]], "fix") for c in cases if c["id"] in fix_runs]
    todo += [(c, r, "control") for c in cases if c.get("kind") == "control"
             for r in runs + sorted(set(fix_runs.values()) - set(runs))]
    for c, run, role in todo:
        found = candidates(c, load_run_findings(run, [c])[c["id"]])
        if role == "control" and not found:  # nothing on that screen in this run: nothing to flag
            continue
        eid = "e%d" % (len(entries) + 1)
        roles[eid] = {"case": c["id"], "run": run, "role": role}
        entries.append({"entry": eid, "case": c["id"], "screen": c["screen"], "match": None,
                        "candidates": [{k2: fd.get(k2) for k2 in ("ref", "title", "actual", "severity", "verdict")}
                                       | {"evidence": (fd.get("evidence") or [None])[0], "run": run}
                                       for fd in found]})
    descriptions = {c["id"]: ("INTENDED, not a bug: " if c.get("kind") == "control" else "")
                    + (c.get("expect") or {}).get("description", "") for c in cases}
    return {"cases": descriptions, "entries": entries}, roles


def score(judged, roles):
    open_ = [e["entry"] for e in judged["entries"] if e.get("match") is None]
    if open_:
        raise FEError("the sheet is not judged yet: %d entries without match (%s)" % (len(open_), ", ".join(open_[:5])))
    hits, fps, control = {}, {}, [0, 0]
    for e in judged["entries"]:
        r = roles[e["entry"]]
        if r["role"] == "control":
            control[0] += 1 if e["match"] else 0
            control[1] += 1
            continue
        bucket = hits.setdefault(r["case"], [0, 0]) if r["role"] == "attempt" else fps.setdefault(r["case"], [0, 0])
        bucket[0] += 1 if e["match"] else 0
        bucket[1] += 1
    out = {cid: {"hits": h, "attempts": n, "caught": h * 3 >= 2 * n > 0,
                 "fix_flagged": "%d/%d" % tuple(fps[cid]) if cid in fps else None}
           for cid, (h, n) in hits.items()}
    return out, {"pairs": sum(f[0] for f in fps.values()), "fix_runs": sum(f[1] for f in fps.values()),
                 "control": "%d/%d" % tuple(control)}


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


def _verdict_counts(runs):
    confirmed = rejected = 0
    for run in runs:
        for p in glob.glob(os.path.join(run, "*", "*", "findings*.json")):
            for fd in read_json(p).get("findings", []):
                confirmed += fd.get("verdict") == "confirmed"
                rejected += fd.get("verdict") == "rejected"
    return confirmed, rejected


def false_clean(judged, roles, cases):
    """Attempt runs whose code verdict was CLEAN although they planned a case's screen and did not catch it."""
    import coverage
    by_id = {c["id"]: c for c in cases}
    missed = {}
    for e in judged["entries"]:
        r = roles[e["entry"]]
        if r["role"] == "attempt" and not e["match"]:
            missed.setdefault(r["run"], []).append(r["case"])
    bad = []
    for run, ids in sorted(missed.items()):
        cov_path = os.path.join(run, "coverage.json")
        if not os.path.exists(cov_path):
            continue
        files = glob.glob(os.path.join(run, "*", "*", "findings*.json"))
        findings, reviewed = coverage._load_findings(files, run)
        cov = read_json(cov_path)
        if coverage.verdict(cov, findings, "en", reviewed)["code"] != "CLEAN":
            continue
        ids = [i for i in ids if _plans(by_id[i], _planned(run))]
        if ids:
            bad.append({"run": run, "missed": ids})
    return bad


def seed(out, src=SEEDED):
    """Copy the seeded packs into a new run folder; each top pack `<state>-<probe>` becomes a done cell."""
    if os.path.exists(out):
        raise FEError("%s exists: seed into a new run folder" % out)
    shutil.copytree(os.path.join(src, "android"), os.path.join(out, "android"))
    with open(os.path.join(src, "spec.md"), encoding="utf-8") as f:
        text = f.read()
    cells, screens = [], []
    for meta in sorted(glob.glob(os.path.join(out, "android", "*", "*", "meta.json"))):
        pack = os.path.dirname(meta)
        screen, name = pack.split(os.sep)[-2:]
        state = next(s for s in sorted(STATES, key=len, reverse=True) if name.startswith(s + "-"))
        probe = name[len(state) + 1:]
        cells.append({"id": "android/%s/%s/%s" % (screen, state, probe), "platform": "android", "screen": screen,
                      "state": state, "probe": probe, "mutating": False, "status": "done",
                      "evidence": [os.path.abspath(pack)], "note": "seeded bench", "marker": []})
        if screen not in screens:
            screens.append(screen)
            os.makedirs(os.path.join(out, "spec"), exist_ok=True)
            with open(os.path.join(out, "spec", screen + ".md"), "w", encoding="utf-8") as f:
                f.write(slice_spec(text, [screen]))
    write_json(os.path.join(out, "coverage.json"), {"run_id": os.path.basename(os.path.abspath(out)), "mode": "bench",
                                                    "cells": cells, "unchecked": [],
                                                    "motion_ceiling_ms": DEFAULT_CEILING_MS})
    return {"out": out, "screens": screens, "cases": os.path.join(src, "cases.json")}


def record(run, scores):
    """The bench a full run was gated on, into its score.json (stats.py keeps it when it scores the run)."""
    path = os.path.join(run, "score.json")
    card = read_json(path) if os.path.exists(path) else {}
    card["bench"] = {"caught": sum(1 for v in scores.values() if v["caught"]), "of": len(scores)}
    write_json(path, card)
    return card["bench"]


def _roles_path(sheet_path):
    return os.path.splitext(sheet_path)[0] + ".roles.json"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sheet")
    p.add_argument("--cases", required=True)
    p.add_argument("--runs", nargs="+", required=True)
    p.add_argument("--fix-runs", nargs="*", default=[], help="CASE_ID=RUN on the build with the fix")
    p.add_argument("--out", required=True)
    p = sub.add_parser("score")
    p.add_argument("--cases", required=True)
    p.add_argument("--sheet", required=True)
    p.add_argument("--out")
    p.add_argument("--run", help="the full run this bench gates: the result goes to its score.json")
    p = sub.add_parser("seed", help="a run folder from the seeded fixtures, every cell done")
    p.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "seed":
        print(json.dumps(seed(a.out)))
        return 0
    cases = read_json(a.cases)
    if a.cmd == "sheet":
        fix = dict(item.split("=", 1) for item in a.fix_runs)
        data, roles = sheet(cases, a.runs, fix)
        write_json(a.out, data)
        write_json(_roles_path(a.out), roles)
        print(json.dumps({"entries": len(data["entries"]), "out": a.out}))
        return 0
    judged, roles = read_json(a.sheet), read_json(_roles_path(a.sheet))
    scores, fps = score(judged, roles)
    runs = sorted({r["run"] for r in roles.values() if r["role"] == "attempt"})
    res = {"cases": scores, "summary": summary(cases, scores, fps, *_verdict_counts(runs)),
           "false_clean": false_clean(judged, roles, cases)}
    if a.out:
        write_json(a.out, res)
    if a.run:
        res["score_json"] = record(a.run, scores)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    cli(main)
