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
SCROLLS = ("ScrollView", "RecyclerView", "ListView", "Table", "CollectionView")


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


def _vertical_scroll(n):
    return n["cls"].endswith(("ScrollView", "RecyclerView", "ListView")) and "Horizontal" not in n["cls"]


def held_short(nodes, screen_wh, px_per_dp=2.625):
    """A list inside a scroll that ends above it with its last row cut to a strip: the list is held to a height
    it had before (laid out while loading, or while a shorter tab was open). The hierarchy reports bounds already
    clipped, so the row only shows as much shorter than its siblings, cut exactly at the list's edge."""
    by_id = {n["id"]: n for n in nodes}
    rows = defaultdict(list)
    for n in nodes:
        if n["rid"] and n.get("parent") in by_id:
            rows[(n["parent"], n["rid"])].append(n)
    result = []
    for (parent, rid), sibs in rows.items():
        if len(sibs) < 3:
            continue
        heights = sorted(s["bounds"][3] for s in sibs)
        usual = heights[len(heights) // 2]
        scrolls = [by_id[a] for a in _ancestors(sibs[0], by_id) if _vertical_scroll(by_id[a])]
        if len(scrolls) < 2:
            continue
        inner, outer = scrolls[0], scrolls[1]
        bottom = inner["bounds"][1] + inner["bounds"][3]
        blank = outer["bounds"][1] + outer["bounds"][3] - bottom
        cut = [s for s in sibs if s["bounds"][3] < 0.6 * usual and abs(s["bounds"][1] + s["bounds"][3] - bottom) <= 2]
        # The band under the list is really empty: a tab bar or a footer there means the screen edge cut the row.
        # Only what starts under the list counts (backgrounds span the screen); off-screen pages are beside it.
        ix0, ix1 = inner["bounds"][0], inner["bounds"][0] + inner["bounds"][2]
        filled = any(n["bounds"][1] >= bottom - 2 and n["bounds"][1] < bottom + blank - 24
                     and min(n["bounds"][0] + n["bounds"][2], ix1) > max(n["bounds"][0], ix0)
                     for n in nodes if n["bounds"][2] > 0 and n["bounds"][3] > 0)
        if cut and blank >= 48 * px_per_dp and not filled:
            x0, y0 = min(s["bounds"][0] for s in cut), min(s["bounds"][1] for s in cut)
            x1 = max(s["bounds"][0] + s["bounds"][2] for s in cut)
            result.append(_cand("held_short", [s["id"] for s in cut], [x0, y0, x1 - x0, bottom - y0],
                                "%s cut to %d%% at its list's bottom edge, %d px of blank under the list"
                                % (rid, round(cut[0]["bounds"][3] * 100.0 / usual), blank), "high"))
    return result


def moved_fraction(before, after):
    """Share of the elements found in both snaps whose bounds changed: 0 means the gesture moved nothing."""
    pairs = _pair_by_key(_app_only(before), _app_only(after))
    if not pairs:
        return 1.0  # nothing in common: the whole screen changed
    return round(sum(b["bounds"] != a["bounds"] for b, a in pairs) / float(len(pairs)), 3)


def _layers(a, b, by_id, paths, screen_area):
    """Both branches below the common ancestor cover half the screen: a layer over a layer (a sheet over a list,
    a search screen over the list it hides, a covered screen of a JS stack), not two elements colliding."""
    pb = set(paths[b["id"]])
    lca = next((i for i in paths[a["id"]] if i in pb), None)
    if lca is None:
        return False
    ra = paths[a["id"]][paths[a["id"]].index(lca) - 1]
    rb = paths[b["id"]][paths[b["id"]].index(lca) - 1]
    return min(_area(by_id[ra]["bounds"]), _area(by_id[rb]["bounds"])) >= 0.5 * screen_area


def overlaps(nodes, min_ratio=0.2, screen_wh=None):
    by_id = {n["id"]: n for n in nodes}
    texty = [n for n in nodes if (n["text"] or n["label"]) and _area(n["bounds"]) > 0]
    paths = {n["id"]: [n["id"]] + _ancestors(n, by_id) for n in texty}
    anc = {nid: set(p[1:]) for nid, p in paths.items()}
    bar = {nid: next((i for i in p if by_id[i]["cls"] in NATIVE_BARS), None) for nid, p in paths.items()}
    screen_area = _area([0, 0] + list(screen_wh)) if screen_wh else None
    result = []
    for i, a in enumerate(texty):
        for b in texty[i + 1:]:
            if a["id"] in anc[b["id"]] or b["id"] in anc[a["id"]] or bar[a["id"]] != bar[b["id"]]:
                continue
            if screen_area and _layers(a, b, by_id, paths, screen_area):
                continue
            inter = _intersect(a["bounds"], b["bounds"])
            smaller = min(_area(a["bounds"]), _area(b["bounds"]))
            if _area(inter) / float(smaller) >= min_ratio:
                result.append(_cand("overlap", [a["id"], b["id"]], inter,
                                    "%r overlaps %r" % (a["text"] or a["label"], b["text"] or b["label"]),
                                    "medium"))
    return _collapse_hubs(result, by_id)


def _collapse_hubs(pairs, by_id, max_pairs=3):
    """One element overlapping many (a huge or stale frame) becomes one candidate, so it cannot eat the crop budget."""
    seen = defaultdict(list)
    for c in pairs:
        for nid in c["node_ids"]:
            seen[nid].append(c)
    hubs = [nid for nid, cs in seen.items() if len(cs) > max_pairs]
    out = [c for c in pairs if not any(nid in hubs for nid in c["node_ids"])]
    for h in hubs:
        n, others = by_id[h], [i for c in seen[h] for i in c["node_ids"] if i != h]
        out.append(_cand("overlap", [h] + others[:5], n["bounds"],
                         "%r overlaps %d elements" % (n["text"] or n["label"], len(others)), "medium"))
    return out


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


def _touching(a, b, eps=1):
    """Sides (l, r, t, b) where box a lies on box b's edge."""
    d = {"l": a[0] - b[0], "r": b[0] + b[2] - a[0] - a[2], "t": a[1] - b[1], "b": b[1] + b[3] - a[1] - a[3]}
    return {s for s, v in d.items() if abs(v) <= eps}


def text_fit(nodes, screen_wh):
    """Text that no longer fits its box (large font, small phone), judged against the texts beside it in the same
    row (segments, tiles, a button pair), which share its font: a word broken over lines («Расписани/е»), a label
    wrapped taller than its row and cut by its button («Удалить / навсегда»), a single line far narrower than
    its letters need («Тём»). uiautomator cuts bounds to the parent, so the cut shows as a box too small."""
    by_id = {n["id"]: n for n in nodes}
    texty = [n for n in nodes if n["text"].strip() and n["bounds"][2] > 0 and n["bounds"][3] > 0
             and not n["cls"].endswith(("EditText", "TextField", "SearchField"))]
    screen = [0, 0] + list(screen_wh)
    result = []
    for n in texty:
        t, (x, y, w, h) = n["text"].strip(), n["bounds"]
        anc = [by_id[a] for a in _ancestors(n, by_id)]
        if _touching(n["bounds"], screen) or any(a["cls"].endswith(SCROLLS) and _touching(n["bounds"], a["bounds"])
                                                 for a in anc):
            continue  # cut by the screen or a list's edge: scrolled, not squeezed
        row = []
        for p in texty:
            (px, py, pw, ph), pt = p["bounds"], p["text"].strip()
            if p is not n and len(pt) >= 3 and min(y + h, py + ph) - max(y, py) >= 0.5 * min(h, ph) \
                    and (px + pw <= x + 2 or px >= x + w - 2) and 0.33 <= pw / _ems(pt) / ph <= 0.6:
                row.append(p)  # beside it in its row, one line at its own width (a stretched box is no measure)
        if not row:
            continue
        line = statistics.median(p["bounds"][3] for p in row)
        btn = next((a for a in anc if a["clickable"]), None)
        same = [p for p in row if abs(p["bounds"][3] - h) <= max(2, 0.06 * h)]
        need = _ems(t) * min(p["bounds"][2] / _ems(p["text"].strip()) for p in same) if same else 0
        if t.isalpha() and len(t) >= 4 and h >= 1.6 * line and w < 1.3 * _ems(t) * TYPICAL_CHAR_PER_H * line:
            detail = "one word %r over %d lines (its row: %d px a line)" % (t, round(h / line), line)
        elif btn and {"t", "b"} <= _touching(n["bounds"], btn["bounds"]) and h >= 1.3 * line:
            detail = "%r wrapped to %d px in a %d px button (its row: %d px a line): cut" % (
                t, h, btn["bounds"][3], line)
        elif sum(c.isalpha() for c in t) >= 4 and w < 0.7 * need:
            detail = "%r gets %d px, its letters need ~%d (same font beside it)" % (t, w, need)
        else:
            continue
        result.append(_cand("text_fit", [n["id"]], n["bounds"], detail, "high"))
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
    by_id = {n["id"]: n for n in nodes}

    def same_control(n):  # iOS cells expose their title as an inner "button" with the cell's own label
        return bool(n["label"]) and any(by_id[a]["clickable"] and by_id[a]["label"] == n["label"]
                                        for a in _ancestors(n, by_id))
    return [_cand("small_target", [n["id"]], n["bounds"],
                  "%dx%d px < %ddp (%.0f px)" % (n["bounds"][2], n["bounds"][3], min_dp, limit), "low")
            for n in nodes
            if n["clickable"] and _area(n["bounds"]) > 0 and (n["bounds"][2] < limit or n["bounds"][3] < limit)
            and not same_control(n)]


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


def _inside(a, b):
    return b[0] <= a[0] and b[1] <= a[1] and a[0] + a[2] <= b[0] + b[2] and a[1] + a[3] <= b[1] + b[3]


def _pinned(b, field, keyboard_top, width):
    """b rides the keyboard: its bottom on the keyboard's top, or (a footer whose padding hides behind the
    keyboard, 0.5.13 trip-wizard) edge to edge, its top below the field's and its bottom past the keyboard's top
    (a list row behind a composer is not edge to edge)."""
    x, top, w, h = b["bounds"]
    return abs(top + h - keyboard_top) <= 2 or \
        x <= 2 and x + w >= width - 2 and field["bounds"][1] < top < keyboard_top <= top + h + 2


def under_bar(nodes, keyboard_top):
    """Keyboard up: a bar pinned on it (a footer with Next or Send) cuts the focused field or the controls under it
    (suggestions, recent chips). uiautomator cuts their bounds at the bar, so they end on its top edge. The bar is
    found by bounds: a footer's button is often not its child in the dump."""
    field = next((n for n in nodes if n["focused"]), None)
    if field is None:
        return []
    taps = [n for n in nodes if n["clickable"] and n["bounds"][3] > 0]
    width = max(n["bounds"][0] + n["bounds"][2] for n in nodes)
    bar = min((b for b in nodes if _pinned(b, field, keyboard_top, width)
               and not _inside(field["bounds"], b["bounds"]) and any(_inside(t["bounds"], b["bounds"]) for t in taps)),
              key=lambda b: b["bounds"][1], default=None)  # the outermost: its top is the edge that cuts
    if bar is None:
        return []
    top = bar["bounds"][1]
    hit = [n for n in [field] + [t for t in taps if t is not field]
           if n["bounds"][3] > 0 and n["bounds"][1] < top <= n["bounds"][1] + n["bounds"][3] + 2]
    if not hit:
        return []
    name = next((t["text"] or t["label"] for t in taps if _inside(t["bounds"], bar["bounds"])
                 and (t["text"] or t["label"])), "")
    what = "the focused field" if field in hit else "%d control(s) below the focused field" % len(hit)
    return [_cand("under_bar", [bar["id"]] + [n["id"] for n in hit],
                  _union([n["bounds"] for n in hit] + [[bar["bounds"][0], top, bar["bounds"][2], 1]]),
                  "%s cut by %r pinned on the keyboard" % (what, name), "high")]


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
    result = partially_visible(nodes, screen) + overlaps(nodes, screen_wh=screen) + truncation_candidates(nodes) \
        + text_fit(nodes, screen) + repeated_strings(nodes) + held_short(nodes, screen, ctx.get("px_per_dp") or 2.625)
    if ctx.get("px_per_dp") and ctx.get("min_dp"):
        result += small_targets(nodes, ctx["px_per_dp"], ctx["min_dp"])
    if ctx.get("keyboard_top") is not None:
        result += under_keyboard(nodes, ctx["keyboard_top"], before)
        result += under_bar(nodes, ctx["keyboard_top"])
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
