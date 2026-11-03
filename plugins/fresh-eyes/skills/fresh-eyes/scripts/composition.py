#!/usr/bin/env python3
"""Composition checks a designer makes at a glance, on the screenshot scaled to 1 px per dp: a blank band
between pieces of content (a gap, or the empty inside of an oversized block), and two tappable controls drawn
almost touching. Output: candidates, like geometry.py.

CLI: composition.py SCREEN.png --nodes nodes.json [--px-per-dp 2.625] [--keyboard-top PX]
"""
import argparse
import json
from collections import Counter

from fe_common import FFMPEG, cli, image_size, read_json, run
from geometry import _ancestors

EDGE_TOL = 16     # gray step that counts as an edge
EMPTY_EDGES = 6   # a blank row still crosses a card's two borders and their anti-aliasing
GAP_DP = 64       # spacing tokens stop at 32-48 dp
WIDE_GAP_DP = 120
BARS_DP = 56      # status bar on top, navigation bar below
BOTTOM_DP = 120   # a tab bar, an input or a CTA pinned above the bottom (or the keyboard): space above it is normal
CONTROL_DP = 80   # taller boxes are cards and tiles, not controls
NEAR_DP = 24      # control pairs this close get the pixel look
CROWD_DP = 8      # Material: 8 dp between touch targets
# Empty inside by design: inputs, maps (flat water), web views. iOS classes carry no package, Android ones do.
FLAT_IOS = {"TextField", "SecureTextField", "SearchField", "TextView", "Map", "WebView"}
FLAT_ANDROID = ("EditText", "TextureView", "SurfaceView", "WebView")
SCROLLS = ("ScrollView", "RecyclerView", "ListView", "Table", "CollectionView")


def gray_dp(src, px_per_dp):
    w, h = image_size(src)
    gw, gh = max(1, round(w / px_per_dp)), max(1, round(h / px_per_dp))
    data = run(FFMPEG + ["-i", src, "-vf", "scale=%d:%d:flags=area,format=gray" % (gw, gh), "-f", "rawvideo",
                         "-frames:v", "1", "-"]).stdout
    return gw, gh, data


def _edges(data, gw, y):
    row = data[y * gw:(y + 1) * gw]
    return sum(1 for a, b in zip(row, row[1:]) if abs(a - b) > EDGE_TOL)


def _explained(nodes, y1, y2, px_per_dp, screen_w):
    """Something the hierarchy knows fills the band: an input, a map or web view across it, or boxes drawn in it
    too pale to make edges (skeleton tiles, a placeholder)."""
    for n in nodes:
        x, y, w, h = (v / px_per_dp for v in n["bounds"])
        inside = min(y + h, y2) - max(y, y1)
        if (n["cls"] in FLAT_IOS or n["cls"].endswith(FLAT_ANDROID)) and inside >= 0.5 * (y2 - y1) \
                and w * px_per_dp >= screen_w / 3:
            return True
        if w >= 32 and h >= 32 and inside >= 0.8 * h and w * px_per_dp < screen_w - 2:
            return True
    return False


def blank_gaps(gray, nodes, px_per_dp, keyboard_top=None):
    gw, gh, data = gray
    bottom = gh - BARS_DP if keyboard_top is None else min(gh - BARS_DP, int(keyboard_top / px_per_dp))
    content = [y for y in range(BARS_DP, bottom) if _edges(data, gw, y) > EMPTY_EDGES]
    out = []
    for a, b in zip(content, content[1:]):
        gap = b - a - 1
        if gap < GAP_DP or b >= bottom - BOTTOM_DP or _explained(nodes, a + 1, b, px_per_dp, gw * px_per_dp):
            continue
        band = Counter(v // 4 for v in data[(a + 1) * gw:b * gw])
        if sum(c for _, c in band.most_common(3)) < 0.9 * gap * gw:
            continue  # a smooth gradient (sky in a photo), not a flat fill
        out.append({"check": "blank_gap", "node_ids": [],
                    "bbox": [0, round((a + 1) * px_per_dp), round(gw * px_per_dp), round(gap * px_per_dp)],
                    "detail": "%d dp blank between content" % gap, "hint": "medium" if gap >= WIDE_GAP_DP else "low"})
    return out


def _clamp(box, gw, gh):
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    return max(0, x1), max(0, y1), min(gw, x2), min(gh, y2)


def _ink(data, gw, gh, box, bg):
    """Bounding box of what is drawn inside `box`: a filled pill, an outline, or bare text and icon glyphs."""
    x1, y1, x2, y2 = _clamp(box, gw, gh)
    ink = None
    for y in range(y1, y2):
        xs = [x1 + i for i, v in enumerate(data[y * gw + x1:y * gw + x2]) if abs(v - bg) > EDGE_TOL]
        if xs:
            ink = [min(xs), y, max(xs) + 1, y + 1] if ink is None else \
                [min(ink[0], xs[0]), ink[1], max(ink[2], xs[-1] + 1), y + 1]
    return ink


def _ring_bg(data, gw, gh, box, pad=4):
    x1, y1, x2, y2 = _clamp([box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad], gw, gh)
    ring = [data[y * gw + x] for y in range(y1, y2) for x in range(x1, x2)
            if y < box[1] or y >= box[3] or x < box[0] or x >= box[2]]
    return Counter(ring).most_common(1)[0][0] if ring else data[0]


def _blank_strip(data, gw, gh, box, bg):
    x1, y1, x2, y2 = _clamp(box, gw, gh)
    return all(abs(data[y * gw + x] - bg) <= EDGE_TOL for y in range(y1, y2) for x in range(x1, x2))


def _cut(n, by_id):
    """Bounds that reach the edge of a scroll along its axis: the visible slice of a tile or row, not the control."""
    for a in _ancestors(n, by_id):
        s = by_id[a]
        if s["cls"].endswith(SCROLLS):
            ax = 0 if "Horizontal" in s["cls"] else 1
            lo, hi = n["bounds"][ax], n["bounds"][ax] + n["bounds"][ax + 2]
            if lo <= s["bounds"][ax] + 2 or hi >= s["bounds"][ax] + s["bounds"][ax + 2] - 2:
                return True
    return False


def _inside(a, b):
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


def crowded(gray, nodes, px_per_dp):
    """Control pairs whose drawn shapes sit closer than CROWD_DP. Bounds alone mislead: padding for the hit
    area makes bounds touch while the pills stand apart, so the gap is measured between the drawn shapes."""
    gw, gh, data = gray
    by_id = {n["id"]: n for n in nodes}
    boxes = []
    for n in nodes:
        x, y, w, h = n["bounds"]
        if n["clickable"] and w > 0 and 0 < h <= CONTROL_DP * px_per_dp and not _cut(n, by_id):
            boxes.append((n, [x / px_per_dp, y / px_per_dp, (x + w) / px_per_dp, (y + h) / px_per_dp]))

    def layered(a):  # a control stacked over another (a hidden page, a button under chips): its pixels are not its own
        return any(not _inside(a, b) and not _inside(b, a) and
                   max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
                   > 0.3 * min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) for _, b in boxes)
    boxes = [(n, b) for n, b in boxes if not layered(b)]
    out = []
    for i, (na, a) in enumerate(boxes):
        for nb, b in boxes[i + 1:]:
            if _inside(a, b) or _inside(b, a):
                continue  # a control within a control
            for ax in (0, 1):  # 0: side by side, 1: stacked
                lo, hi = (a, b) if a[ax] <= b[ax] else (b, a)
                o = 1 - ax  # the other axis must overlap by half the smaller box
                overlap = min(a[o + 2], b[o + 2]) - max(a[o], b[o])
                if overlap < 0.5 * min(a[o + 2] - a[o], b[o + 2] - b[o]) or hi[ax] - lo[ax + 2] > NEAR_DP:
                    continue
                if hi[ax] < lo[ax + 2]:  # padded bounds overlap: split the shared strip, or each sees the other's pill
                    m = (hi[ax] + lo[ax + 2]) / 2
                    lo, hi = list(lo), list(hi)
                    lo[ax + 2], hi[ax] = m, m
                union = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                bg = _ring_bg(data, gw, gh, union)
                ink_lo, ink_hi = _ink(data, gw, gh, lo, bg), _ink(data, gw, gh, hi, bg)
                if not ink_lo or not ink_hi:
                    continue
                gap = ink_hi[ax] - ink_lo[ax + 2]
                strip = list(union)
                strip[ax], strip[ax + 2] = ink_lo[ax + 2], ink_hi[ax]
                if not 0 < gap < CROWD_DP - 1 or not _blank_strip(data, gw, gh, strip, bg):  # 8 dp measures 7
                    continue  # apart enough, or one drawn group (segmented control, joined buttons)
                name = lambda n: n["text"] or n["label"] or n["rid"].split("/")[-1] or n["cls"].split(".")[-1]
                px = [round(v * px_per_dp) for v in union]
                out.append({"check": "crowded", "node_ids": [na["id"], nb["id"]],
                            "bbox": [px[0], px[1], px[2] - px[0], px[3] - px[1]],
                            "detail": "%r and %r drawn %d dp apart (< %d dp)" % (name(na), name(nb), gap, CROWD_DP),
                            "hint": "medium" if gap < 4 else "low"})
    return out


def run_all(src, nodes, px_per_dp, keyboard_top=None):
    gray = gray_dp(src, px_per_dp)
    return blank_gaps(gray, nodes, px_per_dp, keyboard_top) + crowded(gray, nodes, px_per_dp)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("screen")
    ap.add_argument("--nodes", required=True)
    ap.add_argument("--px-per-dp", type=float, default=2.625)
    ap.add_argument("--keyboard-top", type=int)
    a = ap.parse_args()
    print(json.dumps(run_all(a.screen, read_json(a.nodes), a.px_per_dp, a.keyboard_top), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    cli(main)
