#!/usr/bin/env python3
"""Pixel tools for evidence: downscale, crop, corner radius, Set-of-Mark overlay, contact sheet.

CLI (the rest is used from capture.py and motion.py):
  pixels.py crop SRC DST --bbox x,y,w,h [--pad 8] [--zoom 1]
"""
import argparse
import json
import math
import os
import shutil

from fe_common import FEError, FFMPEG, cli, image_size, read_gray, read_rgb, run, write_png

DIFF_TOL = 24  # max per-channel difference still counted as "background"


def _parent(dst):
    d = os.path.dirname(dst)
    if d:
        os.makedirs(d, exist_ok=True)
    return dst


def changed_fraction(a, b, top=0.06, bottom=0.04, w=90, h=200):
    """Share of the app area (status and navigation bars cut off) that differs between two screenshots.
    A clock tick or a blinking cursor stays near 0; a theme or font change moves most of the screen."""
    ga, gb = read_gray(a, w, h), read_gray(b, w, h)
    rows = range(int(h * top), h - int(h * bottom))
    idx = [r * w + c for r in rows for c in range(w)]
    return sum(1 for i in idx if abs(ga[i] - gb[i]) > 24) / float(len(idx))


def downscale(src, dst, long_side=1000):
    _parent(dst)
    w, h = image_size(src)
    if max(w, h) <= long_side:
        if src != dst:
            shutil.copyfile(src, dst)
        return w, h
    if h >= w:
        nw, nh = int(round(w * long_side / float(h))), long_side
    else:
        nw, nh = long_side, int(round(h * long_side / float(w)))
    run(FFMPEG + ["-i", src, "-vf", "scale=%d:%d" % (nw, nh), dst])
    return nw, nh


def _clamp_box(bbox, w, h, pad):
    x1 = max(0, bbox[0] - pad)
    y1 = max(0, bbox[1] - pad)
    x2 = min(w, bbox[0] + bbox[2] + pad)
    y2 = min(h, bbox[1] + bbox[3] + pad)
    if x2 <= x1 or y2 <= y1:
        raise FEError("bbox %s is outside the %dx%d image" % (bbox, w, h))
    return x1, y1, x2 - x1, y2 - y1


def crop(src, dst, bbox, pad=8, zoom=1):
    _parent(dst)
    w, h = image_size(src)
    x, y, cw, ch = _clamp_box(bbox, w, h, pad)
    vf = "crop=%d:%d:%d:%d" % (cw, ch, x, y)
    if zoom and zoom != 1:
        vf += ",scale=%d:%d:flags=neighbor" % (cw * zoom, ch * zoom)
    run(FFMPEG + ["-i", src, "-vf", vf, dst])
    return dst


_DIRS = {"tl": (1, 1), "tr": (-1, 1), "bl": (1, -1), "br": (-1, -1)}


def _radius_in(w, h, data, bbox, corner):
    dx, dy = _DIRS[corner]
    x0 = bbox[0] if dx > 0 else bbox[0] + bbox[2] - 1
    y0 = bbox[1] if dy > 0 else bbox[1] + bbox[3] - 1

    def px(x, y):
        x = min(max(x, 0), w - 1)
        y = min(max(y, 0), h - 1)
        i = (y * w + x) * 3
        return data[i:i + 3]

    bg = px(x0 - 2 * dx, y0 - 2 * dy)
    limit = min(bbox[2], bbox[3]) // 2
    for t in range(limit + 1):
        p = px(x0 + t * dx, y0 + t * dy)
        if max(abs(p[i] - bg[i]) for i in range(3)) > DIFF_TOL:
            return t / (1 - 1 / math.sqrt(2))
    return float("inf")


def corner_radius(src, bbox, corner):
    """Estimate a corner's radius in px: walk the diagonal inward from the bbox corner until the shape starts.
    For a circle arc of radius r the diagonal meets it at t = r(1 - 1/sqrt2)."""
    if corner not in _DIRS:
        raise FEError("corner must be one of tl, tr, bl, br")
    w, h, data = read_rgb(src)
    return _radius_in(w, h, data, bbox, corner)


SURFACE_MIN_SHARE = 0.12  # panels, sheets, dialogs, big cards
SHARP_PX = 4


def corner_candidates(src, nodes, screen_wh, max_surfaces=5):
    """Big surfaces with visibly sharp corners. Corners on the left/right screen edge are skipped:
    edge-to-edge surfaces show no corner there."""
    sw, sh = screen_wh
    w, h, data = read_rgb(src)
    surfaces = [n for n in nodes if n["bounds"][2] * n["bounds"][3] >= SURFACE_MIN_SHARE * sw * sh
                and not (n["bounds"][2] >= sw - 2 and n["bounds"][3] >= sh - 2)
                and not n.get("rid", "").startswith("com.android.systemui")]
    out, seen = [], set()
    for n in sorted(surfaces, key=lambda n: -n["bounds"][2] * n["bounds"][3]):
        box = tuple(n["bounds"])
        if box in seen:
            continue
        seen.add(box)
        x, y, bw, bh = box
        sharp = []
        for corner in ("tl", "tr", "bl", "br"):
            cx = x if corner[1] == "l" else x + bw
            if cx <= 1 or cx >= sw - 1:
                continue
            if _radius_in(w, h, data, list(box), corner) < SHARP_PX:
                sharp.append(corner)
        if sharp:
            out.append({"check": "sharp_corners", "node_ids": [n["id"]], "bbox": list(box), "corners": sharp,
                        "detail": "radius < %d px at %s" % (SHARP_PX, ", ".join(sharp)), "hint": "low"})
        if len(out) >= max_surfaces:
            break
    return out


def tile(srcs, dst, cols=4, frame_h=300):
    if not srcs:
        raise FEError("tile needs at least one image")
    _parent(dst)
    w, h = image_size(srcs[0])
    fw = int(round(w * frame_h / float(h) / 2.0)) * 2
    rows = int(math.ceil(len(srcs) / float(cols)))
    args = []
    for s in srcs:
        args += ["-i", s]
    chains = ";".join("[%d]scale=%d:%d[v%d]" % (i, fw, frame_h, i) for i in range(len(srcs)))
    layout = "|".join("%d_%d" % ((i % cols) * fw, (i // cols) * frame_h) for i in range(len(srcs)))
    inputs = "".join("[v%d]" % i for i in range(len(srcs)))
    grid = "%s xstack=inputs=%d:layout=%s:fill=black,pad=%d:%d:0:0:black" % (
        inputs, len(srcs), layout, fw * cols, frame_h * rows)
    if len(srcs) == 1:
        grid = "[v0]pad=%d:%d:0:0:black" % (fw * cols, frame_h)
    run(FFMPEG + args + ["-filter_complex", chains + ";" + grid, "-frames:v", "1", dst])
    return dst


# 3x5 bitmap digits for Set-of-Mark labels (ffmpeg here has no drawtext).
_DIGITS = {
    "0": ["111", "101", "101", "101", "111"], "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"], "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"], "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"], "7": ["111", "001", "010", "010", "010"],
    "8": ["111", "101", "111", "101", "111"], "9": ["111", "101", "111", "001", "111"],
}
_COLORS = [(230, 25, 75), (0, 130, 200), (60, 180, 75), (245, 130, 48), (145, 30, 180), (0, 128, 128)]


def _fill(buf, w, h, x, y, rw, rh, color):
    for yy in range(max(0, y), min(h, y + rh)):
        start = (yy * w + max(0, x)) * 3
        end = (yy * w + min(w, x + rw)) * 3
        if end > start:
            buf[start:end] = bytes(color) * ((end - start) // 3)


def _label(buf, w, h, x, y, text, color, scale):
    cw, chh = 3 * scale, 5 * scale
    _fill(buf, w, h, x, y, (cw + scale) * len(text) + scale, chh + 2 * scale, color)
    for k, ch in enumerate(text):
        for r, row in enumerate(_DIGITS[ch]):
            for c, bit in enumerate(row):
                if bit == "1":
                    _fill(buf, w, h, x + scale + k * (cw + scale) + c * scale, y + scale + r * scale, scale, scale,
                          (255, 255, 255))


def som_overlay(src, items, dst, max_items=60):
    """Draw numbered boxes over the screenshot; return [{n, node_ids, bbox}]."""
    w, h, data = read_rgb(src)
    buf = bytearray(data)
    scale = max(4, w // 120)  # digits stay readable after the 1000 px downscale
    t = max(3, w // 360)
    mapping = []
    for i, it in enumerate(items[:max_items]):
        box = it.get("bbox") or it.get("bounds")
        ids = it.get("node_ids") or ([it["id"]] if "id" in it else [])
        n = i + 1
        color = _COLORS[i % len(_COLORS)]
        x, y, bw, bh = box
        for (fx, fy, fw_, fh_) in ((x, y, bw, t), (x, y + bh - t, bw, t), (x, y, t, bh), (x + bw - t, y, t, bh)):
            _fill(buf, w, h, fx, fy, fw_, fh_, color)
        _label(buf, w, h, max(0, x), max(0, y - 7 * scale) if y > 7 * scale else y, str(n), color, scale)
        mapping.append({"n": n, "node_ids": ids, "bbox": box})
    write_png(dst, w, h, buf)
    return mapping


def _bbox_arg(s):
    return [int(v) for v in s.split(",")]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("crop"); p.add_argument("src"); p.add_argument("dst")
    p.add_argument("--bbox", type=_bbox_arg, required=True); p.add_argument("--pad", type=int, default=8)
    p.add_argument("--zoom", type=int, default=1)
    a = ap.parse_args()
    print(json.dumps(crop(a.src, a.dst, a.bbox, a.pad, a.zoom), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    cli(main)
