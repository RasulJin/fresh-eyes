#!/usr/bin/env python3
"""Deterministic layout checks over normalized nodes. Output: candidates, not verdicts.

Candidate: {check, node_ids, bbox:[x,y,w,h], detail, hint: high|medium|low}
CLI: geometry.py --nodes nodes.json --ctx ctx.json [--before nodes_before.json] --out candidates.json
  ctx: {"screen_wh": [w,h], "px_per_dp": f, "min_dp": 48|44, "keyboard_top": int|null, "keyboard": bool}
"""
import argparse
import json
import statistics
from collections import defaultdict

from fe_common import cli, read_json, write_json
from hierarchy import match_key

SYSTEM_PREFIXES = ("com.android.systemui",)
# iOS native bars are translucent and content scrolls under them by design; a RN header drawn as a View is not
# one of these and is still checked.
NATIVE_BARS = {"NavigationBar", "Toolbar", "TabBar"}


def _area(b):
    return max(0, b[2]) * max(0, b[3])


def _intersect(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]


def _union(boxes):
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[0] + b[2] for b in boxes)
    y2 = max(b[1] + b[3] for b in boxes)
    return [x1, y1, x2 - x1, y2 - y1]


def _cand(check, ids, bbox, detail, hint):
    return {"check": check, "node_ids": list(ids), "bbox": list(bbox), "detail": detail, "hint": hint}


def _ancestors(node, by_id):
    seen = []
    p = node.get("parent")
    while p is not None and p in by_id:
        seen.append(p)
        p = by_id[p].get("parent")
    return seen


def _meaningful(n):
    return bool(n["text"] or n["label"] or n["clickable"])


def _visible_fractions(nodes, screen_wh):
    by_id = {n["id"]: n for n in nodes}
    screen = [0, 0, screen_wh[0], screen_wh[1]]
    screen_area = _area(screen)
    out = {}
    for n in nodes:
        area = _area(n["bounds"])
        if area == 0 or not (_meaningful(n) or area >= 0.01 * screen_area):
            continue
        clip = screen
        for pid in _ancestors(n, by_id):
            pb = by_id[pid]["bounds"]
            if _area(pb) < 0.98 * screen_area:
                clip = _intersect(pb, screen)
                break
        out[n["id"]] = _area(_intersect(n["bounds"], clip)) / float(area)
    return out


def partially_visible(nodes, screen_wh, min_frac=0.05, max_frac=0.6):
    by_id = {n["id"]: n for n in nodes}
    fracs = _visible_fractions(nodes, screen_wh)
    flagged = {nid for nid, f in fracs.items() if min_frac <= f <= max_frac}
    result = []
    for n in nodes:
        if n["id"] in flagged and not any(a in flagged for a in _ancestors(n, by_id)):
            result.append(_cand("partially_visible", [n["id"]], n["bounds"],
                                "only %d%% visible" % round(fracs[n["id"]] * 100), "low"))
    return result


def _pair_by_key(before, after):
    groups = defaultdict(list)
    for n in before:
        groups[match_key(n)].append(n)
    pairs, used = [], defaultdict(int)
    for n in after:
        k = match_key(n)
        i = used[k]
        if i < len(groups[k]):
            pairs.append((groups[k][i], n))
            used[k] += 1
    return pairs


def still_clipped(before, after, screen_wh):
    """Nodes partially visible before AND after a swipe inside their container, visibility unchanged."""
    fb = {c["node_ids"][0] for c in partially_visible(before, screen_wh)}
    fa = {c["node_ids"][0] for c in partially_visible(after, screen_wh)}
    vb, va = _visible_fractions(before, screen_wh), _visible_fractions(after, screen_wh)
    result = []
    for b, a in _pair_by_key(before, after):
        if b["id"] in fb and a["id"] in fa and abs(va[a["id"]] - vb[b["id"]]) <= 0.05:
            result.append(_cand("clipped", [a["id"]], a["bounds"],
                                "still %d%% visible after swipe inside its container" % round(va[a["id"]] * 100),
                                "high"))
    return result


def overlaps(nodes, min_ratio=0.2):
    by_id = {n["id"]: n for n in nodes}
    texty = [n for n in nodes if (n["text"] or n["label"]) and _area(n["bounds"]) > 0]
    anc = {n["id"]: set(_ancestors(n, by_id)) for n in texty}
    bar = {nid: next((i for i in [nid] + list(a) if by_id[i]["cls"] in NATIVE_BARS), None) for nid, a in anc.items()}
    result = []
    for i, a in enumerate(texty):
        for b in texty[i + 1:]:
            if a["id"] in anc[b["id"]] or b["id"] in anc[a["id"]] or bar[a["id"]] != bar[b["id"]]:
                continue
            inter = _intersect(a["bounds"], b["bounds"])
            smaller = min(_area(a["bounds"]), _area(b["bounds"]))
            if _area(inter) / float(smaller) >= min_ratio:
                result.append(_cand("overlap", [a["id"], b["id"]], inter,
                                    "%r overlaps %r" % (a["text"] or a["label"], b["text"] or b["label"]),
                                    "medium"))
    return result


TYPICAL_CHAR_PER_H = 0.42  # avg glyph width / line-box height when no peers are available
NARROW_RATIO = 0.8          # flag when px-per-char is below this share of what peers of the same height get
_NARROW = set("iljtfrI.,:;!|'\"()[] \u00a0\u00b7-")
_WIDE = set("mwMWШЩЖМЮФшщжмюф@%")


def _ems(text):
    """Text length in average-glyph units, so Latin 'Intelligence' is not mistaken for a squeezed Cyrillic word."""
    return sum(0.45 if c in _NARROW else 1.4 if c in _WIDE else 1.15 if c.isupper() else 1.0 for c in text)


def truncation_candidates(nodes):
    """Text boxes are content-sized unless something constrains them; a cut text gets fewer px per char
    than single-line peers of the same height."""
    texty = [n for n in nodes if n["text"] and n["bounds"][3] > 0 and n["bounds"][2] > 0]
    if not texty:
        return []
    median_h = statistics.median(n["bounds"][3] for n in texty)
    single = [n for n in texty if n["bounds"][3] <= 1.8 * median_h]
    result = []
    for n in single:
        w, h = n["bounds"][2], n["bounds"][3]
        if len(n["text"]) < 8:
            continue
        peers = [p["bounds"][2] / _ems(p["text"]) for p in single
                 if p is not n and abs(p["bounds"][3] - h) <= 0.25 * h and len(p["text"]) >= 4]
        typical = statistics.median(peers) if len(peers) >= 3 else TYPICAL_CHAR_PER_H * h
        per_char = w / _ems(n["text"])
        if per_char < NARROW_RATIO * typical:
            result.append(_cand("truncation", [n["id"]], n["bounds"],
                                "%r: %.1f px/char vs ~%.1f for same-height text" % (n["text"], per_char, typical),
                                "medium"))
    return result


def _norm_text(t):
    return " ".join(t.casefold().split())


def repeated_strings(nodes, min_count=3, min_len=8):
    groups = defaultdict(list)
    for n in nodes:
        if n["text"]:
            groups[_norm_text(n["text"])].append(n)
    result = []
    for text, group in groups.items():
        if len(group) >= min_count and len(text) >= min_len:
            result.append(_cand("repeated", [n["id"] for n in group], _union([n["bounds"] for n in group[:3]]),
                                "%r appears %d times" % (group[0]["text"], len(group)), "medium"))
    return result


def small_targets(nodes, px_per_dp, min_dp):
    limit = px_per_dp * min_dp
    return [_cand("small_target", [n["id"]], n["bounds"],
                  "%dx%d px < %ddp (%.0f px)" % (n["bounds"][2], n["bounds"][3], min_dp, limit), "low")
            for n in nodes
            if n["clickable"] and _area(n["bounds"]) > 0 and (n["bounds"][2] < limit or n["bounds"][3] < limit)]


def under_keyboard(after, keyboard_top, before=None):
    if keyboard_top is None:
        return []
    result = []
    for n in after:
        if n["focused"] and n["bounds"][1] + n["bounds"][3] > keyboard_top:
            result.append(_cand("under_keyboard", [n["id"]], n["bounds"], "focused field below keyboard top",
                                "high"))
    if before is not None:
        old = {match_key(n) for n in before}
        for n in after:
            center_y = n["bounds"][1] + n["bounds"][3] / 2.0
            if match_key(n) not in old and not n["focused"] and _meaningful(n) and center_y > keyboard_top:
                result.append(_cand("under_keyboard", [n["id"]], n["bounds"],
                                    "appeared after focus but hidden under keyboard: %r" % (n["text"] or n["label"]),
                                    "high"))
    return result


def keyboard_missing(nodes, screen_wh):
    """The navigator focused an input (--keyboard) but no soft keyboard is up: focus without keyboard, or on iOS
    the one-time slide-to-type intro / a connected hardware keyboard."""
    focused = [n for n in nodes if n["focused"]]
    w, h = screen_wh
    bbox = focused[0]["bounds"] if focused else [0, h // 2, w, h - h // 2]  # where the keyboard should be
    return [_cand("keyboard_missing", [n["id"] for n in focused[:1]], bbox,
                  "an input was focused but no soft keyboard is shown", "medium")]


def _app_only(nodes):
    return [n for n in nodes if not n["rid"].startswith(SYSTEM_PREFIXES)]


def run_all(nodes, ctx, before=None):
    nodes = _app_only(nodes)
    before = _app_only(before) if before is not None else None
    screen = ctx["screen_wh"]
    result = partially_visible(nodes, screen) + overlaps(nodes) + truncation_candidates(nodes) \
        + repeated_strings(nodes)
    if ctx.get("px_per_dp") and ctx.get("min_dp"):
        result += small_targets(nodes, ctx["px_per_dp"], ctx["min_dp"])
    if ctx.get("keyboard_top") is not None:
        result += under_keyboard(nodes, ctx["keyboard_top"], before)
    elif ctx.get("keyboard"):
        result += keyboard_missing(nodes, screen)
    if before is not None:
        result += still_clipped(before, nodes, screen)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--nodes", required=True)
    ap.add_argument("--ctx", required=True)
    ap.add_argument("--before")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    before = read_json(args.before) if args.before else None
    cands = run_all(read_json(args.nodes), read_json(args.ctx), before)
    write_json(args.out, cands)
    print(json.dumps({"candidates": len(cands), "out": args.out}))
    return 0


if __name__ == "__main__":
    cli(main)
