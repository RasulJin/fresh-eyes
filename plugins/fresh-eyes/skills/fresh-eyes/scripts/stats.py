#!/usr/bin/env python3
"""Score card of a finished run (`coverage.py score`): the metrics of the 9.5 plan a script can count, from the run
folder and, with the session id, the session's transcripts. The judge writes M4, M7 and M11 into score.json
(`"M4": {"value": 0.04}`) and may correct a counted one (`"M1": {"value": 1, "by": "judge"}`: not every numbered
step of run-log.md is a hand move); a rerun keeps them, and every metric's `note` and `by`.
"""
import glob
import json
import os
import re
import time
from collections import Counter
from datetime import datetime

import coverage
from fe_common import read_json, write_json

PROJECTS = os.path.expanduser("~/.claude/projects")  # Claude Code keeps session transcripts here
IDLE_S = 600
JUDGE = ("M4", "M7", "M11")
LIMITS = {  # what each number is and when it passes
    "M1": ("manual orchestrator moves: numbered items of run-log.md (or the report's run log)", lambda v: v <= 1),
    "M2": ("false BUGs (a 'spec text:' or 'known candidate:' reject too, a 'flaky:' one and a same_as_auto copy "
           "not): rejected / ruled",
           lambda v: v <= 0.10),
    "M3": ("BUGs left unsure / ruled", lambda v: v <= 0.15),
    "M4": ("judge: owner-facing BUGs ruled intended or duplicate / owner-facing BUGs", lambda v: v <= 0.05),
    "M5": ("questions to the owner in report.md: a screen's sub-items, a spec-text one none", lambda v: v <= 10),
    "M6": ("confirmed BUGs the verdict turned unsure for a stale spec", lambda v: v == 0),
    "M7": ("judge: done cells whose oracle was not checked", lambda v: v == 0),
    "M8": ("report.md lines", lambda v: v <= 300),
    "M9": ("hours from the run's first file to report.md / gaps over 10 min with nobody working",
           lambda v: v["hours"] <= 4 and v["idle_gaps"] == 0),
    "M10": ("cache_read tokens of the session in the run's window: all / the orchestrator's",
            lambda v: v["total"] <= 100e6 and v["orchestrator"] <= 35e6),
    "M11": ("judge: human-only bug classes caught, of 5", lambda v: v >= 4),
    "M12": ("TaskStop calls / cells blocked by a consent dialog",
            lambda v: v["stops"] == 0 and v["consent_blocked"] == 0),
}
ITEM = re.compile(r"(?:[-*]|\d+\.)\s")
NUMBERED = re.compile(r"\d+\.\s")
SPEC_TEXT = re.compile(r"\s*(?:[-*]|\d+\.)\s+(?:\*\*[^*]*\*\*:?\s*)?spec text:", re.I)
SCREEN = re.compile(r"(?:[-*]|\d+\.)\s+\*\*[^*]+\*\*:?\s*$")  # `- **feed**:` alone: its sub-items are the questions
SUB = re.compile(r"\s+(?:[-*]|\d+\.)\s")
FLAKY = re.compile(r"\s*flaky:", re.I)  # seen once, not reproduced in the repeats: not disproved, so not a false BUG
CONSENT = re.compile(r"\bconsent:", re.I)  # the navigator's blocked note, "consent: <the dialog's title>"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


RUN_LOG = re.compile(r"^#+\s+(ход прогона|run log)", re.I | re.M)  # no log anywhere: M1 unknown, not 0


def items(text, heading, pattern=ITEM):
    """Top-level list items under the headings matching `heading`, each up to the next heading of its level."""
    n, level = 0, None
    for line in text.splitlines():
        h = re.match(r"(#+)\s+(.*)", line)
        if h and (level is None or len(h.group(1)) <= level):
            level = len(h.group(1)) if re.match(heading, h.group(2), re.I) else None
        elif not h and level is not None and pattern.match(line):
            n += 1
    return n


def questions(text):
    """M5: under a questions heading, a screen's bullet (`- **feed**:` alone) counts its sub-items, any other
    bullet once (its sub-items are its options); a `spec text:` item counts none."""
    n, level, group = 0, None, False
    for line in text.splitlines():
        h = re.match(r"(#+)\s+(.*)", line)
        if h and (level is None or len(h.group(1)) <= level):
            level, group = (len(h.group(1)) if re.match(r"(вопрос|question)", h.group(2), re.I) else None), False
        elif not h and level is not None:
            if ITEM.match(line):
                group = bool(SCREEN.match(line))
                n += not (group or SPEC_TEXT.match(line))
            elif group and SUB.match(line):
                n += not SPEC_TEXT.match(line)
    return n


def _transcript(path, lo, hi):
    """(timestamps, cache_read, TaskStop calls, context per turn) of the records inside [lo, hi]; a message's usage
    counts once, its context is all its input tokens (fresh, read from cache and written to it)."""
    ts, cache, stops, seen, ctx = [], 0, 0, set(), []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
                t = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if not lo <= t <= hi:
                continue
            ts.append(t)
            m = d.get("message")
            if d.get("type") != "assistant" or not isinstance(m, dict):
                continue
            stops += sum(1 for c in m.get("content") or [] if isinstance(c, dict) and c.get("name") == "TaskStop")
            if m.get("usage") and m.get("id") not in seen:
                seen.add(m.get("id"))
                u = m["usage"]
                cache += u.get("cache_read_input_tokens") or 0
                ctx.append(sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens",
                                                       "cache_creation_input_tokens")))
    return ts, cache, stops, ctx


def session(sid, lo, hi):
    """Idle gaps, cache_read and TaskStop calls of the orchestrator's transcript and its subagents', and the
    orchestrator's turns and context at its first and last turn of the run, or None."""
    main = glob.glob(os.path.join(PROJECTS, "*", sid + ".jsonl"))
    if not main:
        return None
    ts, orch, stops, ctx = _transcript(main[0], lo, hi)
    total = orch
    for sub in glob.glob(os.path.join(os.path.dirname(main[0]), sid, "subagents", "*.jsonl")):
        t, cache, _, _ = _transcript(sub, lo, hi)
        ts += t
        total += cache
    ts.sort()
    return {"idle_gaps": sum(1 for a, b in zip(ts, ts[1:]) if b - a > IDLE_S), "orchestrator": orch,
            "total": total, "stops": stops, "turns": len(ctx), "context_start": ctx[0] if ctx else None,
            "context_end": ctx[-1] if ctx else None}


def score(run, sid=None, out=None):
    out = out or os.path.join(run, "score.json")
    report = os.path.join(run, "report.md")
    text = _read(report) if os.path.exists(report) else None
    log = os.path.join(run, "run-log.md")
    m = {"M1": sum(1 for line in _read(log).splitlines() if NUMBERED.match(line)) if os.path.exists(log)
         else items(text, r"(ход прогона|run log)", NUMBERED) if text and RUN_LOG.search(text) else None,
         "M5": questions(text) if text is not None else None,
         "M8": len(text.splitlines()) if text is not None else None}
    paths = sorted(glob.glob(os.path.join(run, "*", "*", "findings*.json")))
    findings, _ = coverage._load_findings(paths, run)  # a confirmed finding an open question asks about: unsure
    # a same_as_auto copy (findings.py same) repeats another screen's ruling: one decision counts once
    bugs = [f for f in findings if f.get("kind", "BUG") == "BUG" and not f.get("same_as_auto")]
    n = Counter(f.get("verdict", "pending") for f in bugs)
    flaky = sum(1 for f in bugs if f.get("verdict") == "rejected" and FLAKY.match(f.get("verify_note") or ""))
    ruled = n["confirmed"] + n["unsure"] + n["rejected"]
    m["M2"] = round((n["rejected"] - flaky) / ruled, 3) if ruled else 0.0
    m["M3"] = round(n["unsure"] / ruled, 3) if ruled else 0.0
    cov = read_json(os.path.join(run, "coverage.json"))
    ss = os.path.join(run, "spec-stale.json")
    m["M6"] = len(coverage.verdict(cov, findings, spec_stale=read_json(ss) if os.path.exists(ss) else None)["stale"])
    lo = min(os.path.getmtime(p) for p in glob.glob(os.path.join(run, "*")) if os.path.isfile(p))
    hi = os.path.getmtime(report) if text is not None else time.time()
    s = session(sid, lo, hi) if sid else None
    m["M9"] = {"hours": round((hi - lo) / 3600, 2), "idle_gaps": s and s["idle_gaps"]}
    m["M10"] = {"total": s and s["total"], "orchestrator": s and s["orchestrator"]}
    m["M12"] = {"stops": s and s["stops"], "consent_blocked": sum(1 for c in cov["cells"] if c["status"] == "blocked"
                                                                  and CONSENT.search(c.get("note") or ""))}
    old = read_json(out) if os.path.exists(out) else {}
    corrected = [k for k in LIMITS if k not in JUDGE and (old.get(k) or {}).get("by") == "judge"]
    for k in JUDGE + tuple(corrected):
        m[k] = (old.get(k) or {}).get("value")
    card = {}
    for k in LIMITS:
        v = m[k]
        known = v is not None and not (isinstance(v, dict) and None in v.values())
        card[k] = {"value": v, "ok": LIMITS[k][1](v) if known else None, "what": LIMITS[k][0]}
        card[k].update((f, old[k][f]) for f in ("by", "note") if f in (old.get(k) or {}))  # the judge's words stay
    card["M6"]["of"] = n["confirmed"]
    card["M2"]["flaky"] = flaky
    card["passed"] = sum(1 for k in LIMITS if card[k]["ok"])
    card["unknown"] = [k for k in LIMITS if card[k]["ok"] is None]
    # П-42: a full run in a new session ends under 120 turns and 250 k of context
    card["orchestrator"] = {k: s[k] for k in ("turns", "context_start", "context_end")} if s else None
    write_json(out, card)
    return {"passed": card["passed"], "unknown": card["unknown"], "transcript": bool(s),
            "orchestrator": card["orchestrator"], "out": out}
