#!/usr/bin/env python3
"""Score fresh-eyes runs against known bugs the way the spec's benchmark asks: code finds the candidates, a model
judges them.

cases.json: [{"id", "screen", "also_screens", "platform", "kind": "bug", "group": "live"|"october"|...,
              "expect": {"description": "what a person sees", "bbox": [x,y,w,h]|null}}]
sheet: for every case and run (the attempts on the build with the bug, the run on the build with the fix), the
       confirmed/unsure findings on the case's screen and platform, and area when a bbox is given (the code check).
       Which run is a fix run goes to <sheet>.roles.json, so the judge cannot lean on it.
judge: one `fresh-eyes:judge` sets "match" on every entry: the ref of the finding that is the case's defect, or false.
score: caught = matched in at least 2/3 of the attempts; a match on the fix build is a false positive; a run whose
       code verdict was CLEAN while it missed a case it planned is a false CLEAN (the number that must stay 0).

CLI: bench.py sheet --cases cases.json --runs RUN [RUN ...] [--fix-runs ID=RUN ...] --out sheet.json
     bench.py score --cases cases.json --sheet sheet.json [--out bench.json]
"""
import argparse
import glob
import json
import os

from fe_common import FEError, cli, read_json, write_json

COUNTED = ("confirmed", "unsure")


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = a[2] * a[3] + b[2] * b[3] - inter
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
    by_platform = {}
    for p in sorted(glob.glob(os.path.join(run, "*", "*", "findings*.json"))):
        for fd in read_json(p).get("findings", []):
            fd = dict(fd, ref="%s#%s" % (os.path.relpath(p, run), fd.get("id")))
            by_platform.setdefault(p.split(os.sep)[-3], []).append(fd)
    return {c["id"]: by_platform.get(c["platform"], []) for c in cases}


def sheet(cases, runs, fix_runs):
    """(sheet for the judge, roles kept apart from it)."""
    entries, roles = [], {}
    todo = [(c, r, "attempt") for c in cases if c.get("kind", "bug") == "bug" for r in runs]
    todo += [(c, fix_runs[c["id"]], "fix") for c in cases if c["id"] in fix_runs]
    for k, (c, run, role) in enumerate(todo):
        found = candidates(c, load_run_findings(run, [c])[c["id"]])
        eid = "e%d" % (k + 1)
        roles[eid] = {"case": c["id"], "run": run, "role": role}
        entries.append({"entry": eid, "case": c["id"], "screen": c["screen"], "match": None,
                        "candidates": [{k2: fd.get(k2) for k2 in ("ref", "title", "actual", "severity", "verdict")}
                                       | {"evidence": (fd.get("evidence") or [None])[0], "run": run}
                                       for fd in found]})
    descriptions = {c["id"]: (c.get("expect") or {}).get("description", "") for c in cases}
    return {"cases": descriptions, "entries": entries}, roles


def score(judged, roles):
    open_ = [e["entry"] for e in judged["entries"] if e.get("match") is None]
    if open_:
        raise FEError("the sheet is not judged yet: %d entries without match (%s)" % (len(open_), ", ".join(open_[:5])))
    hits, fps = {}, {}
    for e in judged["entries"]:
        r = roles[e["entry"]]
        bucket = hits.setdefault(r["case"], [0, 0]) if r["role"] == "attempt" else fps.setdefault(r["case"], [0, 0])
        bucket[0] += 1 if e["match"] else 0
        bucket[1] += 1
    out = {cid: {"hits": h, "attempts": n, "caught": h * 3 >= 2 * n > 0,
                 "fix_flagged": "%d/%d" % tuple(fps[cid]) if cid in fps else None}
           for cid, (h, n) in hits.items()}
    return out, {"pairs": sum(f[0] for f in fps.values()), "fix_runs": sum(f[1] for f in fps.values())}


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
        planned = {(c["platform"], c["screen"]) for c in cov["cells"]}
        ids = [i for i in ids if any((by_id[i]["platform"], s) in planned
                                     for s in [by_id[i]["screen"]] + by_id[i].get("also_screens", []))]
        if ids:
            bad.append({"run": run, "missed": ids})
    return bad


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
    a = ap.parse_args()
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
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    cli(main)
