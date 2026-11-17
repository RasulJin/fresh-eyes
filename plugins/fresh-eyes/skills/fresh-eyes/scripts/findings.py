#!/usr/bin/env python3
"""One case, one check in a run: findings of different screens that report the same case are linked, and what the
verifiers settled as test data or deliberate goes to known-proposed.md, so the next run's reviewers skip it.

same:  before each verifier's first pass, and once more before the verdict. A pending BUG of a --findings file that
       reports the case of a BUG in another screen folder (same platform; at least 3 word stems of the titles shared
       and half of the shorter title's; when both titles quote on-screen text, a quote in common) gets `same_as` to
       it and `same_as_auto`: only that one is verified. A linked finding whose target is ruled (or became an IMP)
       takes its kind, verdict and severity, again whenever the target's ruling changes; so does a pending one a
       reviewer marked same_as (a consequence of another finding; a bare id is one of its own file). Only --findings
       files are written: never a file a verifier holds.
       Prints `verify`: the --findings files that still need a verifier (a pending BUG not linked, or requests),
       and `links`: every [copy, target] of those files, for run-log.md (a wrong link hides a bug: a human checks).
known: appends to OUT the run's `known` questions and the BUGs rejected as `test data:`, each text once.

CLI: findings.py same --run RUN --findings F [F ...]
     findings.py known --run RUN --out .fresh-eyes/known-proposed.md
"""
import argparse
import glob
import json
import os
import re

from backlog import _stems
from fe_common import cli, read_json, write_json

QUOTE = re.compile(r"«([^»]+)»|“([^”]+)”|\"([^\"]+)\"")
TEST_DATA = re.compile(r"\s*test data:", re.I)
HEADER = ("# Known candidates\n\nProposed by fresh-eyes verifiers: test data and choices a code comment calls "
          "deliberate. Reviewers do not flag them. The owner moves each to known.md or deletes it.\n\n")


def _quotes(title):
    return {next(g for g in m.groups() if g).strip().lower() for m in QUOTE.finditer(title)}


def shared(a, b):
    """Stems two findings share when they report one case, else 0."""
    if a.get("platform") != b.get("platform"):
        return 0
    sa, sb = _stems(a.get("title", "")), _stems(b.get("title", ""))
    n = len(sa & sb)
    qa, qb = _quotes(a.get("title", "")), _quotes(b.get("title", ""))
    if n < 3 or n < min(len(sa), len(sb)) / 2.0 or (qa and qb and not qa & qb):
        return 0
    return n


def _files(run, paths):
    """(path, data, the path as given when it is one of `paths`) of every findings file of the run and `paths`."""
    mine = {os.path.abspath(p): p for p in paths}
    every = sorted({os.path.abspath(p) for p in glob.glob(os.path.join(run, "*", "*", "findings*.json"))} | set(mine))
    return [(p, read_json(p), mine.get(p)) for p in every]


def same(run, paths):
    files = _files(run, paths)
    refs = {}  # ref -> finding
    for p, data, _ in files:
        for f in data.get("findings", []):
            refs["%s#%s" % (os.path.relpath(p, run), f.get("id"))] = f
    anchors = []  # (folder, ref, finding): verified or held elsewhere, or the first of its case
    todo = []
    for p, data, writable in files:
        for f in data.get("findings", []):
            if f.get("kind", "BUG") != "BUG" or f.get("same_as"):
                continue
            item = (os.path.dirname(p), "%s#%s" % (os.path.relpath(p, run), f.get("id")), f)
            (todo if writable and f.get("verdict", "pending") == "pending" else anchors).append(item)
    linked = settled = 0
    for folder, ref, f in todo:
        best = max(((shared(f, a), aref) for afolder, aref, a in anchors if afolder != folder), default=(0, None))
        if best[0]:
            f["same_as"], f["same_as_auto"] = best[1], True
            linked += 1
        else:
            anchors.append((folder, ref, f))
    changed, links = set(), []
    for p, data, writable in files:
        rel = os.path.relpath(p, run)
        for f in data.get("findings", []):
            auto = f.get("same_as_auto")
            # a reviewer's same_as (a consequence of another finding) still pending takes that one's ruling too
            if not writable or not f.get("same_as") or not (auto or f.get("verdict", "pending") == "pending"):
                continue
            ref = f["same_as"] if "#" in f["same_as"] else "%s#%s" % (rel, f["same_as"])
            link = ["%s#%s" % (rel, f.get("id")), ref]
            if auto:
                changed.add(p)  # a link made above or earlier
                links.append(link)
            t = refs.get(ref)
            if t is None or (t.get("kind", "BUG") == "BUG" and t.get("verdict", "pending") == "pending"):
                continue
            ruling = {k: t[k] for k in ("kind", "verdict", "severity", "backlog_id") if k in t}
            ruling["verify_note"] = "%s (same case: %s)" % (t.get("verify_note", ""), ref)
            if any(f.get(k) != v for k, v in ruling.items()):  # a target ruled again (CONFLICT) moves its copies
                f.update(ruling)
                settled += 1
                if not auto:
                    changed.add(p)
                    links.append(link)
    for p, data, writable in files:
        if p in changed:
            write_json(p, data)
    verify = [writable for p, data, writable in files if writable and (data.get("requests") or any(
        f.get("kind", "BUG") == "BUG" and f.get("verdict", "pending") == "pending" and not f.get("same_as_auto")
        for f in data.get("findings", [])))]
    return {"linked": linked, "settled": settled, "verify": verify, "links": links}


def known(run, out):
    lines = []
    for p, data, _ in _files(run, []):
        for q in data.get("questions") or []:
            if isinstance(q, dict) and q.get("class") == "known" and q.get("text"):
                lines.append(q["text"].strip())
        for f in data.get("findings", []):
            note = f.get("verify_note") or ""
            if f.get("verdict") == "rejected" and not f.get("same_as_auto") and TEST_DATA.match(note):
                lines.append("%s: «%s» — %s" % (f.get("screen"), f.get("title", ""), note.strip()))
    text = HEADER
    if os.path.exists(out):
        with open(out, encoding="utf-8") as fh:
            text = fh.read()
    have = {line[2:].strip() for line in text.splitlines() if line.startswith("- ")}
    new = [line for line in dict.fromkeys(lines) if " ".join(line.split()) not in have]
    if new:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text.rstrip("\n") + "\n" + "".join("- %s\n" % " ".join(line.split()) for line in new))
    return {"added": len(new), "out": out}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("same", help="link one case's findings across screens; copy the ruling to the links")
    p.add_argument("--run", required=True)
    p.add_argument("--findings", nargs="+", required=True, help="the files going to a verifier now (or all)")
    p = sub.add_parser("known", help="append the run's known candidates and test data to known-proposed.md")
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    a = ap.parse_args()
    out = same(a.run, a.findings) if a.cmd == "same" else known(a.run, a.out)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    cli(main)
