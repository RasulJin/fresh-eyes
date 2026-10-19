#!/usr/bin/env python3
"""Pixel tools for evidence: downscale, crop, corner radius, side-by-side, Set-of-Mark overlay, contact sheet.

CLI:
  pixels.py downscale SRC DST [--long-side 1000]
  pixels.py crop SRC DST --bbox x,y,w,h [--pad 8] [--zoom 1]
  pixels.py corner SRC --bbox x,y,w,h --corner tl|tr|bl|br
  pixels.py side DST SRC [SRC ...] [--height 160]
  pixels.py som SRC ITEMS.json DST          (items: candidates or nodes; prints the number→node mapping)
  pixels.py tile DST SRC [SRC ...] [--cols 4] [--frame-h 300]
"""
import argparse
import json
import math
import os
import shutil

from fe_common import FEError, FFMPEG, cli, image_size, read_json, read_rgb, run, write_png

DIFF_TOL = 24  # max per-channel difference still counted as "background"


def _parent(dst):
    d = os.path.dirname(dst)
    if d:
        os.makedirs(d, exist_ok=True)
    return dst


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


def corner_radius(src, bbox, corner):
    """Estimate a corner's radius in px: walk the diagonal inward from the bbox corner until the shape starts.
    For a circle arc of radius r the diagonal meets it at t = r(1 - 1/sqrt2)."""
    if corner not in _DIRS:
        raise FEError("corner must be one of tl, tr, bl, br")
    w, h, data = read_rgb(src)
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


def side_by_side(srcs, dst, height=160):
    if len(srcs) < 2:
        raise FEError("side_by_side needs at least two images")
    _parent(dst)
    args = []
    for s in srcs:
        args += ["-i", s]
    chains = ";".join("[%d]scale=-2:%d[v%d]" % (i, height, i) for i in range(len(srcs)))
    stack = "".join("[v%d]" % i for i in range(len(srcs))) + "hstack=inputs=%d" % len(srcs)
    run(FFMPEG + args + ["-filter_complex", chains + ";" + stack, "-frames:v", "1", dst])
    return dst


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
    p = sub.add_parser("downscale"); p.add_argument("src"); p.add_argument("dst")
    p.add_argument("--long-side", type=int, default=1000)
    p = sub.add_parser("crop"); p.add_argument("src"); p.add_argument("dst")
    p.add_argument("--bbox", type=_bbox_arg, required=True); p.add_argument("--pad", type=int, default=8)
    p.add_argument("--zoom", type=int, default=1)
    p = sub.add_parser("corner"); p.add_argument("src"); p.add_argument("--bbox", type=_bbox_arg, required=True)
    p.add_argument("--corner", required=True, choices=sorted(_DIRS))
    p = sub.add_parser("side"); p.add_argument("dst"); p.add_argument("srcs", nargs="+")
    p.add_argument("--height", type=int, default=160)
    p = sub.add_parser("som"); p.add_argument("src"); p.add_argument("items"); p.add_argument("dst")
    p = sub.add_parser("tile"); p.add_argument("dst"); p.add_argument("srcs", nargs="+")
    p.add_argument("--cols", type=int, default=4); p.add_argument("--frame-h", type=int, default=300)
    a = ap.parse_args()
    if a.cmd == "downscale":
        out = downscale(a.src, a.dst, a.long_side)
    elif a.cmd == "crop":
        out = crop(a.src, a.dst, a.bbox, a.pad, a.zoom)
    elif a.cmd == "corner":
        out = round(corner_radius(a.src, a.bbox, a.corner), 1)
    elif a.cmd == "side":
        out = side_by_side(a.srcs, a.dst, a.height)
    elif a.cmd == "som":
        out = som_overlay(a.src, read_json(a.items), a.dst)
    else:
        out = tile(a.srcs, a.dst, a.cols, a.frame_h)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    cli(main)
