#!/usr/bin/env python3
"""Coverage plan, cell status, verdict and coverage map for a fresh-eyes run; changed files → screens.

The verdict is computed here, never by a model. Mutating probes only enter the plan when preflight
confirmed a staging/local backend.

CLI:
  coverage.py plan --config C --preflight P --mode quick|full [--screens a,b] [--recheck BUG-1,BUG-2]
                   [--max-screens 4] --out cov.json
  coverage.py set --cov cov.json --cell ID --status todo|done|failed|blocked|na [--evidence P ...] [--note T]
  coverage.py next --cov cov.json [--platform android|ios] [--limit 10]
  coverage.py verdict --cov cov.json --findings F [F ...] [--language ru|en]
  coverage.py map --cov cov.json [--language ru|en]
  coverage.py diff-screens --repo R --base REF --config C
  coverage.py prune --runs .fresh-eyes/runs [--keep 3]
"""
import argparse
import glob
import json
import os
import re
import time
from collections import Counter, defaultdict, deque

from fe_common import FEError, cli, locked, read_json, run, write_json

PLATFORMS = ["android", "ios"]
BOTH = ["android", "ios"]
PROBES = {
    "none": {"mutating": False, "platforms": BOTH},
    "rapid-tap": {"mutating": False, "platforms": BOTH},
    "tap-outside": {"mutating": False, "platforms": BOTH},
    "back-during-animation": {"mutating": False, "platforms": BOTH},
    "hardware-back": {"mutating": False, "platforms": BOTH},  # iOS: the left-edge swipe
    "hardware-back-keyboard": {"mutating": False, "platforms": ["android"]},
    "background-foreground": {"mutating": False, "platforms": BOTH},
    "kill-relaunch": {"mutating": False, "platforms": BOTH},
    "keyboard-open": {"mutating": False, "platforms": BOTH},
    "search": {"mutating": False, "platforms": BOTH},  # type a query, run a read-only search, look at the results
    "scenario": {"mutating": False, "platforms": BOTH},  # the screen's task end to end; writes only if mutation is on
    "swipe-inside": {"mutating": False, "platforms": BOTH},
    "tap-everything": {"mutating": False, "platforms": BOTH},
    "rotate": {"mutating": False, "platforms": BOTH},
    "offline": {"mutating": False, "platforms": ["android"]},
    "replay": {"mutating": False, "platforms": BOTH},  # quick BUG-N: replay the backlog entry's steps
    "edit-delete-restore": {"mutating": True, "platforms": BOTH},
    "double-submit": {"mutating": True, "platforms": BOTH},
}
# snaps inside the pack a done cell needs: a navigator skips a probe's extra steps unless the cell depends on them
REQUIRED_SUBPACKS = {"rapid-tap": "afterback", "keyboard-open": "return", "search": "typed"}
# a scenario does the task, changes an input after the result, tries an invalid input, interrupts an action,
# and checks what was kept
SCENARIO_KINDS = {"task", "change", "invalid", "interrupt", "check"}
# probes after which the app has no reason to be on another screen: their pack must show the screen's marker
# (not swipe-inside: scrolling takes headers, and with them most markers, off screen)
ON_SCREEN_PROBES = {"none", "keyboard-open"}
STATES = ["default", "empty", "loading", "error", "offline", "long-text", "long-list", "dark", "font-xl",
          "small-screen", "keyboard"]
STATE_PLATFORMS = {"offline": ["android"]}
NOT_AUTOMATED = {"offline"}  # on the other platform this is a gap to report, not "not applicable"
STATE_NEEDS = {"empty": "seed", "long-text": "seed", "long-list": "seed", "loading": "hook", "error": "hook",
               "small-screen": "small_device"}
STATUSES = ("todo", "done", "failed", "blocked", "na")
LABELS = {
    "ru": {"BLOCK": "НЕ ВЫПУСКАТЬ", "ISSUES": "ЕСТЬ ПРОБЛЕМЫ", "INCOMPLETE": "НЕ ПРОВЕРЕНО ДО КОНЦА",
           "CLEAN": "ЧИСТО"},
    "en": {"BLOCK": "DO NOT SHIP", "ISSUES": "HAS ISSUES", "INCOMPLETE": "NOT FULLY CHECKED", "CLEAN": "CLEAN"},
}
CODE_EXT = (".ts", ".tsx", ".js", ".jsx")


def _cell(platform, screen, state, probe):
    return {"id": "%s/%s/%s/%s" % (platform, screen, state, probe), "platform": platform, "screen": screen,
            "state": state, "probe": probe, "mutating": PROBES[probe]["mutating"], "status": "todo",
            "evidence": [], "note": ""}


QUICK_MAX_SCREENS = 4


def plan(config, preflight, mode, screens=None, recheck=(), max_screens=QUICK_MAX_SCREENS, passes=1):
    by_id = {s["id"]: s for s in config.get("screens", [])}
    missing = [s for s in screens or () if s not in by_id]
    if missing:
        raise FEError("unknown screens: %s (known: %s)" % (", ".join(missing), ", ".join(sorted(by_id))))
    if max_screens < 1:
        raise FEError("--max-screens must be at least 1")
    recheck = list(dict.fromkeys(recheck))  # a twin cell would stay todo forever: set updates the first id only
    unchecked = []
    if mode == "full":
        chosen = list(dict.fromkeys(screens or by_id))
    else:  # quick stays small: named screens only (none = only the recheck cells), neighbors when named
        chosen = list(dict.fromkeys(screens or ()))
        unchecked += [{"what": "%s: all states" % s, "why": "over the quick limit of %d screens" % max_screens}
                      for s in chosen[max_screens:]]
        chosen = chosen[:max_screens]
        near = [n for s in chosen for n in by_id[s].get("neighbors", []) if n in by_id and n not in screens]
        unchecked += [{"what": "%s: all states" % n, "why": "neighbor of a checked screen; quick checks it when named"}
                      for n in dict.fromkeys(near)]
    plats = preflight.get("platforms", {})
    targets = config.get("platforms", PLATFORMS)
    ok = [p for p in targets if plats.get(p, {}).get("ok")]
    unchecked += [{"what": "%s: all screens" % p, "why": "no ready device or build (see preflight)"}
                  for p in targets if p not in ok]
    hooks = config.get("hooks") or {}
    mutating = bool(preflight.get("mutating_allowed"))
    cells = []
    for p in ok:
        for bug in recheck:
            cells.append(_cell(p, "recheck-" + bug, "default", "replay"))
        small = bool(plats[p].get("small_device"))
        for sid in chosen:
            s = by_id[sid]
            for state in s.get("states", ["default"]):
                if state not in STATES:
                    raise FEError("screen %s: unknown state %s" % (sid, state))
                if p not in STATE_PLATFORMS.get(state, BOTH):
                    if state in NOT_AUTOMATED:
                        unchecked.append({"what": "%s/%s/%s" % (p, sid, state), "why": "not automated on %s" % p})
                    continue
                need = STATE_NEEDS.get(state)
                why = None
                if need == "seed" and not (hooks.get("seed") and mutating):
                    why = "needs hooks.seed and a verified staging/local backend (seeding writes data)"
                elif need == "hook" and not hooks.get(state):
                    why = "needs hooks.%s (nothing can produce this state otherwise)" % state
                elif need == "small_device" and not small:
                    why = "needs a small device"
                if why:
                    unchecked.append({"what": "%s/%s/%s" % (p, sid, state), "why": why})
                    continue
                cells.append(_cell(p, sid, state, "none"))
            for probe in s.get("probes", []):
                if probe not in PROBES:
                    raise FEError("screen %s: unknown probe %s" % (sid, probe))
                if probe == "none" or p not in PROBES[probe]["platforms"]:
                    if probe in NOT_AUTOMATED:
                        unchecked.append({"what": "%s/%s/%s" % (p, sid, probe), "why": "not automated on %s" % p})
                    continue
                if PROBES[probe]["mutating"] and not preflight.get("mutating_allowed"):
                    unchecked.append({"what": "%s/%s/%s" % (p, sid, probe),
                                      "why": "mutating probe disabled: backend not verified as staging/local"})
                    continue
                cells.append(_cell(p, sid, "default", probe))
                if probe == "scenario":  # writes (send, edit, delete) when the backend allows it, reads otherwise
                    cells[-1]["mutating"] = mutating
    for j in config.get("journeys", []):
        if mode == "quick" and not set(j.get("screens", [])) & set(chosen):
            continue
        for p in ok:
            if j.get("mutating") and not preflight.get("mutating_allowed"):
                unchecked.append({"what": "%s/journey/%s" % (p, j["id"]),
                                  "why": "mutating journey disabled: backend not verified as staging/local"})
                continue
            cells.append({"id": "%s/journey/%s/journey" % (p, j["id"]), "platform": p, "screen": "journey",
                          "state": j["id"], "probe": "journey", "mutating": bool(j.get("mutating")),
                          "status": "todo", "evidence": [], "note": j.get("steps", "")})
    for c in cells:  # what `set` checks an on-screen pack and a scenario against
        if by_id.get(c["screen"], {}).get("marker"):
            c["marker"] = list(by_id[c["screen"]]["marker"])
        if c["probe"] == "scenario" and by_id[c["screen"]].get("forms"):
            c["forms"] = list(by_id[c["screen"]]["forms"])
    if passes == 2:  # one pass catches ~75-80% of what two independent passes do: a release gets a second one
        cells += [dict(c, id=c["id"] + "#2", evidence=[], **{"pass": 2}) for c in cells
                  if not c["screen"].startswith("recheck-")]
    return {"run_id": time.strftime("%Y%m%d-%H%M") + "-" + mode, "mode": mode, "cells": cells,
            "unchecked": unchecked}


def _find(cov, cell_id):
    for c in cov["cells"]:
        if c["id"] == cell_id:
            return c
    raise FEError("unknown cell: %s" % cell_id)


def _evidence_problem(path):
    """Why a pack cannot prove a done cell, or None. Journey packs hold step-N packs."""
    if not os.path.isdir(path):
        return "%s does not exist" % path
    packs = [path] + [os.path.join(path, d) for d in sorted(os.listdir(path))
                      if os.path.isfile(os.path.join(path, d, "meta.json"))]
    for pack in packs:
        meta = os.path.join(pack, "meta.json")
        if os.path.exists(meta) and read_json(meta).get("blocked"):
            return "%s is blocked: %s" % (pack, read_json(meta)["blocked"])
        around = os.path.join(pack, "around.json")
        if os.path.exists(around) and read_json(around).get("action_exit", 0) != 0:
            return "the recorded action failed in %s (exit %s)" % (pack, read_json(around)["action_exit"])
    if not any(os.path.exists(os.path.join(pk, f)) for pk in packs for f in ("meta.json", "around.json")):
        return "%s has no capture (meta.json / around.json)" % path
    return None


def _shows_marker(pack, markers):
    """True/False from the pack's nodes (text, label or testID contains a marker); None when it has none to check."""
    path = os.path.join(pack, "nodes.json")
    if not os.path.isfile(path):
        return None
    want = [m.casefold() for m in markers]
    return any(w in (n.get(k) or "").casefold()
               for n in read_json(path) for k in ("text", "label", "rid") for w in want)


def _scenario_problems(pack, cell):
    path = os.path.join(pack, "steps.json")
    steps = read_json(path) if os.path.isfile(path) else []
    kinds = {s.get("kind") for s in steps}
    problems = ["steps.json has no %s step" % "/".join(sorted(SCENARIO_KINDS - kinds))] \
        if SCENARIO_KINDS - kinds else []
    android = cell["platform"] == "android"
    # a tab switch is not an interruption: stop the running action, and cut the network (Android only)
    how = {s.get("how") for s in steps if s.get("kind") == "interrupt"}
    missing = sorted({"stop", "offline"} - how if android else {"stop"} - how)
    if "interrupt" in kinds and missing:
        problems.append("an interrupt step with how=%s" % " and how=".join(missing))
    # a form counts only through a task step that has its own snap: naming it in a step that did not run is not enough
    done = " ".join("%s %s" % (s.get("did", ""), s.get("via", "")) for s in steps if s.get("kind") == "task"
                    and os.path.isfile(os.path.join(pack, "step-%s" % s.get("n"), "meta.json"))).casefold()
    problems += ["a task step with its own snap <pack>/step-<n> through the form «%s» (config forms)" % f
                 for f in cell.get("forms", []) if f.casefold() not in done]
    if android and not os.path.isfile(os.path.join(pack, "logcat.txt")):
        problems.append("no logcat.txt (capture.py logs)")
    return problems


def set_status(cov, cell_id, status, evidence=None, note=""):
    if status not in STATUSES:
        raise FEError("status must be one of %s" % ", ".join(STATUSES))
    c = _find(cov, cell_id)
    if status in ("na", "blocked", "failed") and not (note or c["note"]):
        raise FEError("--status %s needs --note saying why" % status)
    if status == "done":
        packs = list(evidence or c["evidence"])
        problems = [pr for pr in (_evidence_problem(e) for e in packs) if pr] or \
            ([] if packs else ["no evidence pack given"])
        problems += ["%s is not under <run>/<platform>/<screen>/" % e for e in packs if pack_key(e) is None]
        need = REQUIRED_SUBPACKS.get(c["probe"])
        if need and packs and not any(os.path.isfile(os.path.join(e, need, "meta.json")) for e in packs):
            problems.append("a %s cell needs the snap <pack>/%s (see probes.md)" % (c["probe"], need))
        back = os.path.join(packs[0], "return", "meta.json") if packs else ""
        if c["probe"] == "keyboard-open" and os.path.isfile(back) and read_json(back).get("keyboard_up"):
            problems.append("the keyboard is still up in <pack>/return, so the state after it closes was never seen: "
                            "close it with one taps.py back (with the keyboard up Back only hides it), wait, snap again")
        swiped = read_json(os.path.join(packs[0], "meta.json")) \
            if c["probe"] == "swipe-inside" and packs and os.path.isfile(os.path.join(packs[0], "meta.json")) else None
        if swiped is not None and "before" not in swiped:
            problems.append("a swipe-inside pack is snapped after the swipe with capture.py snap --before <the pack "
                            "snapped before it>, so the scripts can tell what moved")
        elif swiped is not None and swiped.get("moved") == 0 and not note:
            problems.append("the swipe moved nothing on screen: swipe inside the list or grid itself, not on a "
                            "header, and longer; if it really does not scroll, pass --note saying so")
        if c["probe"] == "scenario" and packs:
            problems += ["a scenario cell needs: %s (see probes.md)" % p for p in _scenario_problems(packs[0], c)]
        if c.get("marker") and c["probe"] in ON_SCREEN_PROBES and not note \
                and all(_shows_marker(e, c["marker"]) is False for e in packs):
            problems.append("the pack shows none of %s's markers %s, so it was snapped on another screen: snap it "
                            "there; if it is that screen and it is broken (blank, an error), pass --note saying "
                            "what it shows" % (c["screen"], c["marker"]))
        if problems:
            raise FEError("cell %s cannot be done: %s. Retry, or mark it failed/blocked with --note"
                          % (cell_id, "; ".join(problems)))
    c["status"] = status
    if evidence:
        c["evidence"] = list(evidence)
    if note:
        c["note"] = note
    return c


def next_cells(cov, platform=None, limit=10, mutating_allowed=True):
    """Todo cells; mutating ones are blocked when the current preflight no longer allows mutation (resume)."""
    for c in cov["cells"]:
        if c["status"] == "todo" and c.get("mutating") and not mutating_allowed:
            c["status"], c["note"] = "blocked", "mutation not allowed by the current preflight"
    return [c for c in cov["cells"] if c["status"] == "todo" and (platform is None or c["platform"] == platform)][:limit]


def pack_key(path):
    """(platform, screen, pack) from any path form of <run>/<platform>/<screen>/<pack>[/...]."""
    parts = os.path.normpath(path).split(os.sep)
    for i in range(len(parts) - 3, -1, -1):
        if parts[i] in PLATFORMS:
            return tuple(parts[i:i + 3])
    return None


def verdict(cov, findings, language="en", reviewed=()):
    """reviewed: pack paths some reviewer listed in reviewed_packs. A done cell none of whose packs was reviewed
    is not checked; a finding still pending was never verified. Neither can end in CLEAN."""
    labels = LABELS.get(language, LABELS["en"])
    merged = [f for f in findings if _merged(f, findings)]
    findings = [f for f in findings if not any(f is m for m in merged)]
    bugs = [f for f in findings if f.get("verdict") == "confirmed" and f.get("kind", "BUG") == "BUG"]
    high = [f for f in bugs if f.get("severity") == "high"]
    medium = [f for f in bugs if f.get("severity") == "medium"]
    pending = [f for f in findings if f.get("verdict", "pending") == "pending"]
    # an intermittent bug the repeat missed ends unsure: a human has to look before anyone says CLEAN
    doubtful = [f for f in findings if f.get("verdict") == "unsure" and f.get("kind", "BUG") == "BUG"
                and f.get("severity") in ("high", "medium")]
    # "na" (not applicable) is reported, never counted as checked: the config should drop that probe instead
    open_cells = [c for c in cov["cells"] if c["status"] != "done"]
    seen = {pack_key(p) for p in reviewed}
    seen.discard(None)
    unreviewed = [c for c in cov["cells"] if c["status"] == "done"
                  and not any(pack_key(e) in seen for e in c["evidence"])]
    reasons = []
    if high:
        reasons.append("%d confirmed high: %s" % (len(high), "; ".join(f.get("title", "") for f in high[:5])))
    if medium:
        reasons.append("%d confirmed medium" % len(medium))
    if not cov["cells"]:
        reasons.append("no cells planned")
    if open_cells:
        na = [c for c in open_cells if c["status"] == "na"]
        reasons.append("%d cells not done%s" % (len(open_cells), " (%d marked not applicable)" % len(na) if na
                                                else ""))
    if unreviewed:
        reasons.append("%d done cells whose evidence no reviewer looked at: %s"
                       % (len(unreviewed), ", ".join(c["id"] for c in unreviewed[:5])))
    if pending:
        reasons.append("%d findings not verified" % len(pending))
    if doubtful:
        reasons.append("%d high/medium findings need a human look (unsure): %s"
                       % (len(doubtful), "; ".join(f.get("title", "") for f in doubtful[:3])))
    if merged:
        reasons.append("%d duplicate findings merged" % len(merged))
    if cov.get("unchecked"):
        reasons.append("%d items not checked" % len(cov["unchecked"]))
    complete = (bool(cov["cells"]) and not open_cells and not unreviewed and not pending and not doubtful
                and not cov.get("unchecked"))
    if high:
        code = "BLOCK"
    elif medium:
        code = "ISSUES" if complete else "INCOMPLETE"
    elif not complete:
        code = "INCOMPLETE"
    else:
        code = "CLEAN"
    return {"code": code, "label": labels[code], "reasons": reasons,
            "merged": ["%s#%s" % (f.get("_file", ""), f.get("id", "")) for f in merged]}


_RANK = {"low": 0, "medium": 1, "high": 2}


def _merged(f, findings):
    """A finding marked same_as another is left out of the counts only when that one already counts at least as
    much: confirmed, same kind, not lower severity, not itself a duplicate. Otherwise it counts on its own."""
    ref = f.get("same_as")
    if not ref or f.get("verdict", "pending") == "pending":  # never verified: it still keeps CLEAN away
        return False
    file, _, fid = ref.partition("#")
    for t in findings:
        if t is not f and t.get("id") == fid and os.path.normpath(t.get("_file", "")) == os.path.normpath(file):
            return (not t.get("same_as") and t.get("verdict") == "confirmed"
                    and t.get("kind", "BUG") == f.get("kind", "BUG")
                    and _RANK.get(t.get("severity"), 0) >= _RANK.get(f.get("severity"), 0))
    return False


_SYMBOL = {"done": "✓", "failed": "✗", "na": "—", "todo": "?", "blocked": "?"}


def coverage_map(cov, language="en"):
    def where(c):  # the second pass gets its own columns
        return c["platform"] + (" #2" if c.get("pass") == 2 else "")
    cols = sorted({"%s/%s" % (c["state"], c["probe"]) for c in cov["cells"]})
    plats = sorted({where(c) for c in cov["cells"]}, key=lambda w: (PLATFORMS.index(w.split()[0]), w))
    grid = defaultdict(dict)
    for c in cov["cells"]:
        grid[c["screen"]]["%s/%s %s" % (c["state"], c["probe"], where(c))] = _SYMBOL[c["status"]]
    heads = ["%s %s" % (col, p) for col in cols for p in plats]
    first = "экран" if language == "ru" else "screen"
    lines = ["| %s | %s |" % (first, " | ".join(heads)), "|---|%s" % ("---|" * len(heads))]
    for screen in sorted(grid):
        lines.append("| %s | %s |" % (screen, " | ".join(grid[screen].get(h, "") for h in heads)))
    return "\n".join(lines)


_IMPORT = re.compile(r"""(?:from\s+|import\s*\(\s*|require\(\s*|import\s+)['"]([^'"]+)['"]""")


def _strip_ext(path):
    base, ext = os.path.splitext(path)
    if ext in CODE_EXT:
        path = base
    if os.path.basename(path) == "index":
        path = os.path.dirname(path)
    return path


def screens_from_diff(repo, base, config):
    expo_root = (config.get("app") or {}).get("expo_root", ".")
    changed = set()
    for i, cmd in enumerate((["diff", "--name-only", "%s...HEAD" % base], ["diff", "--name-only"],
                             ["ls-files", "--others", "--exclude-standard"])):
        out = run(["git", "-C", repo] + cmd, check=i == 0).stdout.decode()  # a bad base must not look like "no change"
        changed.update(line.strip() for line in out.splitlines() if line.strip())
    routes = defaultdict(list)
    for s in config.get("screens", []):
        routes[os.path.normpath(os.path.join(expo_root, s["route"]))].append(s["id"])
    importers = defaultdict(set)  # module path without extension -> files importing it
    root_abs = os.path.join(repo, expo_root)
    for dirpath, dirnames, filenames in os.walk(root_abs):
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".git", "ios", "android", ".expo")]
        for fn in filenames:
            if not fn.endswith(CODE_EXT):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), repo)
            try:
                with open(os.path.join(repo, rel), encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            for spec in _IMPORT.findall(text):
                if spec.startswith("."):
                    target = os.path.normpath(os.path.join(os.path.dirname(rel), spec))
                elif spec.startswith("@/"):
                    target = os.path.normpath(os.path.join(expo_root, spec[2:]))
                else:
                    continue
                importers[_strip_ext(target)].add(rel)
    hits, unmapped, root = Counter(), [], os.path.normpath(expo_root)
    for f in sorted(changed):
        if not f.endswith(CODE_EXT):
            continue
        found = set(routes.get(os.path.normpath(f), []))
        seen, queue = {f}, deque([(f, 0)])
        while queue:
            cur, depth = queue.popleft()
            found.update(routes.get(os.path.normpath(cur), []))
            if depth >= 3:
                continue
            for imp in importers.get(_strip_ext(os.path.normpath(cur)), ()):
                if imp not in seen:
                    seen.add(imp)
                    queue.append((imp, depth + 1))
        for s in found:  # a file shared by many screens counts less than a screen's own file
            hits[s] += 1.0 / len(found)
        if not found and (root in (".", "") or os.path.normpath(f).startswith(root + os.sep)):
            unmapped.append(f)
    # most-hit first: quick keeps the first QUICK_MAX_SCREENS
    return {"screens": sorted(hits, key=lambda s: (-hits[s], s)), "unmapped": unmapped}


def _load_findings(paths, run="."):
    """All findings (each tagged with its file relative to the run, for same_as) and all reviewed pack paths."""
    findings, reviewed = [], []
    for p in paths:
        try:
            data = read_json(p)
        except (OSError, ValueError) as e:
            raise FEError("cannot read findings file %s: %s" % (p, e))
        items = data.get("findings", []) if isinstance(data, dict) else data
        for f in items:
            f["_file"] = os.path.relpath(p, run)
        findings += items
        reviewed += data.get("reviewed_packs", []) if isinstance(data, dict) else []
    return findings, reviewed


BULKY = ("screen.png", "video.mp4")  # ~60% of a run; screen_1000.png, som.png, crops and sheets stay


def prune(runs_dir, keep=3):
    """Runs older than the newest `keep` lose their full-size screenshots and videos, unless a finding links one.
    Folder names start with the date, so name order is age order. Returns the bytes freed."""
    runs = sorted(d for d in glob.glob(os.path.join(runs_dir, "*")) if os.path.isdir(d))
    freed = 0
    for run in runs[:-keep] if keep > 0 else runs:
        linked = set()
        for f in glob.glob(os.path.join(run, "*", "*", "findings*.json")):
            for fd in read_json(f).get("findings", []):
                linked.update(os.path.normpath(os.path.join(run, e)) for e in fd.get("evidence") or [])
        for root, _, files in os.walk(run):
            for name in files:
                path = os.path.normpath(os.path.join(root, name))
                if name in BULKY and path not in linked:
                    freed += os.path.getsize(path)
                    os.remove(path)
    return freed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--config", required=True); p.add_argument("--preflight", required=True)
    p.add_argument("--mode", required=True, choices=["quick", "full"]); p.add_argument("--screens")
    p.add_argument("--recheck", help="comma list of backlog ids to replay (quick BUG-N)")
    p.add_argument("--max-screens", type=int, default=QUICK_MAX_SCREENS, help="quick only")
    p.add_argument("--passes", type=int, choices=[1, 2], default=1, help="2: a second independent pass (release)")
    p.add_argument("--out", required=True)
    p = sub.add_parser("set"); p.add_argument("--cov", required=True); p.add_argument("--cell", required=True)
    p.add_argument("--status", required=True, choices=STATUSES); p.add_argument("--evidence", nargs="*")
    p.add_argument("--note", default="")
    p = sub.add_parser("next"); p.add_argument("--cov", required=True); p.add_argument("--platform")
    p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("verdict"); p.add_argument("--cov", required=True); p.add_argument("--findings", nargs="*",
                                                                                        default=[])
    p.add_argument("--language", default="en")
    p = sub.add_parser("map"); p.add_argument("--cov", required=True); p.add_argument("--language", default="en")
    p = sub.add_parser("diff-screens"); p.add_argument("--repo", required=True); p.add_argument("--base",
                                                                                              required=True)
    p.add_argument("--config", required=True)
    p = sub.add_parser("prune"); p.add_argument("--runs", required=True); p.add_argument("--keep", type=int, default=3)
    a = ap.parse_args()
    if a.cmd == "plan":
        cov = plan(read_json(a.config), read_json(a.preflight), a.mode, a.screens.split(",") if a.screens else None,
                   a.recheck.split(",") if a.recheck else (), a.max_screens, a.passes)
        write_json(a.out, cov)
        print(json.dumps({"cells": len(cov["cells"]), "unchecked": len(cov["unchecked"]), "out": a.out}))
    elif a.cmd == "set":
        with locked(a.cov):
            cov = read_json(a.cov)
            set_status(cov, a.cell, a.status, a.evidence, a.note)
            write_json(a.cov, cov)
        print(json.dumps({"cell": a.cell, "status": a.status}))
    elif a.cmd == "next":
        pf = os.path.join(os.path.dirname(os.path.abspath(a.cov)), "preflight.json")
        allowed = os.path.exists(pf) and read_json(pf).get("mutating_allowed") is True
        with locked(a.cov):
            cov = read_json(a.cov)
            cells = next_cells(cov, a.platform, a.limit, allowed)
            write_json(a.cov, cov)
        print(json.dumps([c["id"] for c in cells], ensure_ascii=False))
    elif a.cmd == "verdict":
        findings, reviewed = _load_findings(a.findings, os.path.dirname(a.cov) or ".")
        print(json.dumps(verdict(read_json(a.cov), findings, a.language, reviewed), ensure_ascii=False))
    elif a.cmd == "map":
        print(coverage_map(read_json(a.cov), a.language))
    elif a.cmd == "prune":
        print(json.dumps({"freed_mb": round(prune(a.runs, a.keep) / 1e6, 1)}))
    else:
        print(json.dumps(screens_from_diff(a.repo, a.base, read_json(a.config)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    cli(main)
