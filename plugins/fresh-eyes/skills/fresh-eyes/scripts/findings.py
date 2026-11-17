#!/usr/bin/env python3
"""One case, one check in a run: findings of different screens that report the same case are linked, and what the
verifiers settled as test data or deliberate goes to known-proposed.md, so the next run's reviewers skip it.

same:  before each verifier's first pass, and once more before the verdict. An IMP that repeats an earlier one (the
       same test, any screen or folder) gets same_as to that one's case and `same_as_auto`. A pending BUG of a --findings file that
       reports the case of a BUG in another screen folder (same platform; at least 3 word stems of the titles shared
       and half of the shorter title's; when both titles quote on-screen text, a quote in common) gets `same_as` to
       it and `same_as_auto`: only that one is verified. A linked finding whose target is ruled (or became an IMP)
       takes its kind, verdict and severity, again whenever the target's ruling changes; so does a pending one a
       reviewer marked same_as (a consequence of another finding; a bare id is one of its own file). Only --findings
       files are written: never a file a verifier holds.
       Prints `verify`: the --findings files that still need a verifier (a pending BUG not linked, or requests),
       and `links`: every [copy, target] of those files, for run-log.md (a wrong link hides a bug: a human checks).
known: appends to OUT the run's `known` questions and the BUGs rejected as `test data:`, each text once.
requests: before a navigator takes the requests: in each screen folder's requests.json, open entries asking for one
       thing (the same pack, do, probe, args, bbox) become one: the first keeps its fields, every `why` and the most
       `times`; the others go into its `merged` as they were (their `finding` and `from` still count).

CLI: findings.py same --run RUN --findings F [F ...]
     findings.py known --run RUN --out .fresh-eyes/known-proposed.md
     findings.py requests --run RUN
"""
import argparse
import glob
import json
import os
import re
from collections import defaultdict

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


def imp_shared(a, b):
    """shared() for two IMPs; when only one title quotes on-screen text, it names an element the other may not
    mean: 5 stems (0.5.13 full: «⋯» hard to see on a photo met the big card on a small screen at 4)."""
    n = shared(a, b)
    return n if n >= 5 or bool(_quotes(a.get("title", ""))) == bool(_quotes(b.get("title", ""))) else 0


def _files(run, paths):
    """(path, data, the path as given when it is one of `paths`) of every findings file of the run and `paths`."""
    mine = {os.path.abspath(p): p for p in paths}
    every = sorted({os.path.abspath(p) for p in glob.glob(os.path.join(run, "*", "*", "findings*.json"))} | set(mine))
    return [(p, read_json(p), mine.get(p)) for p in every]


def _full(ref, rel):
    """A same_as as `<file relative to the run>#<id>`: a bare id is one of the file `rel`."""
    return ref if "#" in ref else "%s#%s" % (rel, ref)


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
    # IMPs get no verifier: one case's IMPs, in one folder too, join the case's first one (0.5.13 full: the theme
    # toggle four times, three in side-menu's own files); a verifier's same_as joins too; imp.md gives one number
    imps = [("%s#%s" % (os.path.relpath(p, run), f.get("id")), f, writable) for p, data, writable in files
            for f in data.get("findings", []) if f.get("kind") == "IMP" and f.get("verdict") != "rejected"]
    first = list(range(len(imps)))

    def head(i):
        while first[i] != i:
            i = first[i]
        return i
    at = {ref: i for i, (ref, _, _) in enumerate(imps)}
    for i, (ref, f, _) in enumerate(imps):
        joins = [j for j in range(i) if imp_shared(f, imps[j][1])]
        joins += [at[t] for t in [_full(f.get("same_as") or "", ref.rpartition("#")[0])] if t in at]
        for j in joins:
            a, b = sorted((head(i), head(j)))
            first[b] = a
    groups = defaultdict(list)
    for i, (_, f, _) in enumerate(imps):
        if not f.get("same_as"):  # the group's own chains end at one of these; the first takes the others
            groups[head(i)].append(i)
    for i in (i for g in groups.values() for i in g[1:] if imps[i][2]):
        imps[i][1]["same_as"], imps[i][1]["same_as_auto"] = imps[groups[head(i)][0]][0], True
        linked += 1
    changed, links = set(), []
    for p, data, writable in files:
        rel = os.path.relpath(p, run)
        for f in data.get("findings", []):
            auto = f.get("same_as_auto")
            ref = _full(f.get("same_as") or "", rel)
            if f.get("kind") == "IMP" and (refs.get(ref) or {}).get("kind") == "IMP":
                if writable and auto:
                    changed.add(p)  # an IMP's copy: no ruling to take, imp.md lists it under its first
                continue
            # a reviewer's same_as (a consequence of another finding) still pending takes that one's ruling too
            if not writable or not f.get("same_as") or not (auto or f.get("verdict", "pending") == "pending"):
                continue
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


ASKS = ("why", "finding", "from", "times", "merged")  # what may differ between two requests for one repeat


def requests(run):
    """Merge each requests.json's open entries that ask for one repeat (0.5.13 full: 44 entries, 36 of them
    without a finding; trip-map asked twice for each of two repeats). Done, failed and redo-cell entries stay."""
    merged, files = 0, []
    for path in sorted(glob.glob(os.path.join(run, "*", "*", "requests.json"))):
        keep, first, before = [], {}, merged
        for r in read_json(path):
            if not isinstance(r, dict) or r.get("done") or r.get("failed") or r.get("do") == "redo-cell":
                keep.append(r)
                continue
            key = json.dumps({k: v for k, v in r.items() if k not in ASKS}, sort_keys=True, ensure_ascii=False)
            t = first.setdefault(key, r)
            if t is r:
                keep.append(r)
                continue
            t.setdefault("merged", []).append(r)
            t["why"] = "\n".join(w for w in (t.get("why"), r.get("why")) if w)
            if r.get("times") or t.get("times"):
                t["times"] = max(r.get("times") or 1, t.get("times") or 1)
            merged += 1
        if merged > before:
            write_json(path, keep)
            files.append(path)
    return {"merged": merged, "files": files}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("same", help="link one case's findings across screens; copy the ruling to the links")
    p.add_argument("--run", required=True)
    p.add_argument("--findings", nargs="+", required=True, help="the files going to a verifier now (or all)")
    p = sub.add_parser("known", help="append the run's known candidates and test data to known-proposed.md")
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("requests", help="merge each screen's open requests that ask for one repeat")
    p.add_argument("--run", required=True)
    a = ap.parse_args()
    out = same(a.run, a.findings) if a.cmd == "same" else known(a.run, a.out) if a.cmd == "known" \
        else requests(a.run)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    cli(main)
