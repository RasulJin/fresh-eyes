#!/usr/bin/env python3
"""Coverage plan, cell status, verdict and coverage map for a fresh-eyes run; changed files → screens.

The verdict is computed here, never by a model. Mutating probes only enter the plan when preflight
confirmed a staging/local backend.

CLI:
  coverage.py plan --config C --preflight P --mode quick|full [--screens a,b] [--recheck BUG-1,BUG-2] --out cov.json
  coverage.py set --cov cov.json --cell ID --status todo|done|failed|blocked|na [--evidence P ...] [--note T]
  coverage.py next --cov cov.json [--platform android|ios] [--limit 10]
  coverage.py verdict --cov cov.json --findings F [F ...] [--language ru|en]
  coverage.py map --cov cov.json [--language ru|en]
  coverage.py diff-screens --repo R --base REF --config C
"""
import argparse
import json
import os
import re
import time
from collections import defaultdict, deque

from fe_common import FEError, cli, read_json, run, write_json

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
    "swipe-inside": {"mutating": False, "platforms": BOTH},
    "tap-everything": {"mutating": False, "platforms": BOTH},
    "rotate": {"mutating": False, "platforms": BOTH},
    "offline": {"mutating": False, "platforms": ["android"]},
    "replay": {"mutating": False, "platforms": BOTH},  # quick BUG-N: replay the backlog entry's steps
    "edit-delete-restore": {"mutating": True, "platforms": BOTH},
    "double-submit": {"mutating": True, "platforms": BOTH},
}
STATES = ["default", "empty", "loading", "error", "offline", "long-text", "dark", "font-xl", "small-screen",
          "keyboard"]
STATE_PLATFORMS = {"offline": ["android"]}
NOT_AUTOMATED = {"offline"}  # on the other platform this is a gap to report, not "not applicable"
STATE_NEEDS = {"empty": "seed", "long-text": "seed", "loading": "hook", "error": "hook",
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


def plan(config, preflight, mode, screens=None, recheck=()):
    by_id = {s["id"]: s for s in config.get("screens", [])}
    if screens:
        missing = [s for s in screens if s not in by_id]
        if missing:
            raise FEError("unknown screens: %s (known: %s)" % (", ".join(missing), ", ".join(sorted(by_id))))
        chosen = list(screens)
        if mode == "quick":
            for s in screens:
                chosen += [n for n in by_id[s].get("neighbors", []) if n in by_id and n not in chosen]
    else:
        chosen = list(by_id)
    plats = preflight.get("platforms", {})
    targets = config.get("platforms", PLATFORMS)
    ok = [p for p in targets if plats.get(p, {}).get("ok")]
    unchecked = [{"what": "%s: all screens" % p, "why": "no ready device or build (see preflight)"}
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


def set_status(cov, cell_id, status, evidence=None, note=""):
    if status not in STATUSES:
        raise FEError("status must be one of %s" % ", ".join(STATUSES))
    c = _find(cov, cell_id)
    if status == "done":
        packs = list(evidence or c["evidence"])
        problems = [pr for pr in (_evidence_problem(e) for e in packs) if pr] or \
            ([] if packs else ["no evidence pack given"])
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
    bugs = [f for f in findings if f.get("verdict") == "confirmed" and f.get("kind", "BUG") == "BUG"]
    high = [f for f in bugs if f.get("severity") == "high"]
    medium = [f for f in bugs if f.get("severity") == "medium"]
    pending = [f for f in findings if f.get("verdict", "pending") == "pending"]
    open_cells = [c for c in cov["cells"] if c["status"] not in ("done", "na")]
    seen = {pack_key(p) for p in reviewed}
    unreviewed = [c for c in cov["cells"] if c["status"] == "done"
                  and not any(pack_key(e) in seen for e in c["evidence"])]
    reasons = []
    if high:
        reasons.append("%d confirmed high: %s" % (len(high), "; ".join(f.get("title", "") for f in high[:5])))
    if medium:
        reasons.append("%d confirmed medium" % len(medium))
    if open_cells:
        reasons.append("%d cells not done" % len(open_cells))
    if unreviewed:
        reasons.append("%d done cells whose evidence no reviewer looked at: %s"
                       % (len(unreviewed), ", ".join(c["id"] for c in unreviewed[:5])))
    if pending:
        reasons.append("%d findings not verified" % len(pending))
    if cov.get("unchecked"):
        reasons.append("%d items not checked" % len(cov["unchecked"]))
    complete = not open_cells and not unreviewed and not pending and not cov.get("unchecked")
    if high:
        code = "BLOCK"
    elif medium:
        code = "ISSUES" if complete else "INCOMPLETE"
    elif not complete:
        code = "INCOMPLETE"
    else:
        code = "CLEAN"
    return {"code": code, "label": labels[code], "reasons": reasons}


_SYMBOL = {"done": "✓", "failed": "✗", "na": "—", "todo": "?", "blocked": "?"}


def coverage_map(cov, language="en"):
    cols = sorted({"%s/%s" % (c["state"], c["probe"]) for c in cov["cells"]})
    plats = [p for p in PLATFORMS if any(c["platform"] == p for c in cov["cells"])]
    grid = defaultdict(dict)
    for c in cov["cells"]:
        grid[c["screen"]]["%s/%s %s" % (c["state"], c["probe"], c["platform"])] = _SYMBOL[c["status"]]
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
    for cmd in (["diff", "--name-only", "%s...HEAD" % base], ["diff", "--name-only"],
                ["ls-files", "--others", "--exclude-standard"]):
        out = run(["git", "-C", repo] + cmd, check=False).stdout.decode()
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
    screens, unmapped = set(), []
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
        if found:
            screens.update(found)
        elif os.path.normpath(f).startswith(os.path.normpath(expo_root)):
            unmapped.append(f)
    return {"screens": sorted(screens), "unmapped": unmapped}


def _load_findings(paths):
    """All findings and all reviewed pack paths from findings.json files."""
    findings, reviewed = [], []
    for p in paths:
        try:
            data = read_json(p)
        except (OSError, ValueError) as e:
            raise FEError("cannot read findings file %s: %s" % (p, e))
        findings += data.get("findings", []) if isinstance(data, dict) else data
        reviewed += data.get("reviewed_packs", []) if isinstance(data, dict) else []
    return findings, reviewed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--config", required=True); p.add_argument("--preflight", required=True)
    p.add_argument("--mode", required=True, choices=["quick", "full"]); p.add_argument("--screens")
    p.add_argument("--recheck", help="comma list of backlog ids to replay (quick BUG-N)")
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
    a = ap.parse_args()
    if a.cmd == "plan":
        cov = plan(read_json(a.config), read_json(a.preflight), a.mode, a.screens.split(",") if a.screens else None,
                   a.recheck.split(",") if a.recheck else ())
        write_json(a.out, cov)
        print(json.dumps({"cells": len(cov["cells"]), "unchecked": len(cov["unchecked"]), "out": a.out}))
    elif a.cmd == "set":
        cov = read_json(a.cov)
        set_status(cov, a.cell, a.status, a.evidence, a.note)
        write_json(a.cov, cov)
        print(json.dumps({"cell": a.cell, "status": a.status}))
    elif a.cmd == "next":
        cov = read_json(a.cov)
        pf = os.path.join(os.path.dirname(os.path.abspath(a.cov)), "preflight.json")
        allowed = os.path.exists(pf) and read_json(pf).get("mutating_allowed") is True
        cells = next_cells(cov, a.platform, a.limit, allowed)
        write_json(a.cov, cov)
        print(json.dumps([c["id"] for c in cells], ensure_ascii=False))
    elif a.cmd == "verdict":
        findings, reviewed = _load_findings(a.findings)
        print(json.dumps(verdict(read_json(a.cov), findings, a.language, reviewed), ensure_ascii=False))
    elif a.cmd == "map":
        print(coverage_map(read_json(a.cov), a.language))
    else:
        print(json.dumps(screens_from_diff(a.repo, a.base, read_json(a.config)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    cli(main)
