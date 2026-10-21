#!/usr/bin/env python3
"""Build the evidence pack for one coverage cell, so the navigator never has to look at pixels.

snap:   screen.png, screen_1000.png, nodes.json, hierarchy.txt, candidates.json, som.png (+som.json),
        crops/<check>-<k>.png (native resolution), meta.json. Exit 3 = blocked (no hierarchy), 4 = screen identical to --expect-differs.
around: records the screen around an action command, then motion analysis: video.mp4, motion.json,
        sheet_*.png, around.json.

CLI:
  capture.py snap --platform P --device ID --out PACK [--hierarchy-file F --hierarchy-source maestro|uiautomator]
                  [--before PACK] [--keyboard]
  capture.py around --platform P --device ID --out PACK [--settle 2.5] [--ceiling-ms 500] -- CMD ...
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

import devices
import fe_common
from devices import Device
from fe_common import FEError, image_size, read_json, write_json
from geometry import run_all
from hierarchy import HierarchyError, compress, normalize
from motion import DEFAULT_CEILING_MS, analyze
from pixels import corner_candidates, crop, downscale, som_overlay

MIN_DP = {"android": 48, "ios": 44}
MAX_CROPS = 12
MAX_MARKS = 60
CORNER_CROP = 96  # px around a corner, zoomed x2 for the reviewer


def _dump_uiautomator(dev):
    out = fe_common.run([devices.adb_path(), "-s", dev.id, "exec-out", "uiautomator", "dump", "/dev/tty"],
                        timeout=40).stdout.decode("utf-8", "replace")
    return out


def _hierarchy(dev, hierarchy_file, source):
    if hierarchy_file:
        with open(hierarchy_file, encoding="utf-8") as f:
            return normalize(f.read(), source or "maestro"), source or "maestro"
    if dev.platform != "android":
        raise HierarchyError("iOS needs --hierarchy-file (Maestro inspect_screen output)")
    last = None
    for _ in range(3):
        try:
            return normalize(_dump_uiautomator(dev), "uiautomator"), "uiautomator"
        except FEError as e:  # HierarchyError is an FEError; adb failures too
            last = e
            time.sleep(0.7)
    raise HierarchyError(str(last))


def _marks(nodes, candidates):
    items = [dict(c) for c in candidates]
    flagged = {i for c in candidates for i in c["node_ids"]}
    parents = {n["parent"] for n in nodes}
    for n in nodes:
        leaf = n["id"] not in parents
        if n["id"] not in flagged and (n["text"] or n["label"] or n["clickable"]) and (leaf or n["clickable"]) \
                and not n["rid"].startswith("com.android.systemui"):
            items.append({"node_ids": [n["id"]], "bbox": n["bounds"]})
    return items[:MAX_MARKS]


def _same_file(a, b):
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return hashlib.md5(fa.read()).digest() == hashlib.md5(fb.read()).digest()
    except OSError:
        return False


def snap(platform, device_id, out, hierarchy_file=None, hierarchy_source=None, before=None, keyboard=False,
         expect_differs=None):
    dev = Device(platform, device_id, "")
    os.makedirs(out, exist_ok=True)
    shot = devices.screenshot(dev, os.path.join(out, "screen.png"))
    w, h = image_size(shot)
    meta = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "platform": platform, "device": device_id,
            "screen_wh": [w, h]}
    if expect_differs and _same_file(shot, os.path.join(expect_differs, "screen.png")):
        meta["blocked"] = "screen identical to %s: the state did not apply, wait and snap again" % expect_differs
        write_json(os.path.join(out, "meta.json"), meta)
        return 4
    try:
        nodes, source = _hierarchy(dev, hierarchy_file, hierarchy_source)
    except FEError as e:
        meta["blocked"] = "hierarchy unavailable: %s" % e
        write_json(os.path.join(out, "meta.json"), meta)
        return 3
    write_json(os.path.join(out, "nodes.json"), nodes)
    with open(os.path.join(out, "hierarchy.txt"), "w", encoding="utf-8") as f:
        f.write(compress(nodes))
    ctx = {"screen_wh": [w, h], "px_per_dp": devices.px_per_dp(dev), "min_dp": MIN_DP.get(platform, 48),
           "keyboard_top": devices.keyboard_top(dev) if keyboard else None}
    before_nodes = read_json(os.path.join(before, "nodes.json")) if before else None
    candidates = run_all(nodes, ctx, before_nodes)
    app_nodes = [n for n in nodes if not n["rid"].startswith("com.android.systemui")]
    candidates += corner_candidates(shot, app_nodes, [w, h])
    write_json(os.path.join(out, "candidates.json"), candidates)
    downscale(shot, os.path.join(out, "screen_1000.png"))
    full_som = os.path.join(out, ".som_full.png")
    mapping = som_overlay(shot, _marks(nodes, candidates), full_som)
    downscale(full_som, os.path.join(out, "som.png"))
    os.remove(full_som)
    write_json(os.path.join(out, "som.json"), mapping)
    shutil.rmtree(os.path.join(out, "crops"), ignore_errors=True)  # a re-snap must not keep crops of an old screen
    counters = {}
    for c in candidates[:MAX_CROPS]:
        counters[c["check"]] = counters.get(c["check"], 0) + 1
        k = counters[c["check"]]
        try:
            if c["check"] == "sharp_corners":
                x, y, bw, bh = c["bbox"]
                for corner in c["corners"]:
                    cx = x if corner[1] == "l" else x + bw - CORNER_CROP
                    cy = y if corner[0] == "t" else y + bh - CORNER_CROP
                    crop(shot, os.path.join(out, "crops", "sharp_corners-%d-%s.png" % (k, corner)),
                         [cx, cy, CORNER_CROP, CORNER_CROP], pad=CORNER_CROP // 2, zoom=2)
            else:
                crop(shot, os.path.join(out, "crops", "%s-%d.png" % (c["check"], k)), c["bbox"])
        except FEError:
            continue  # bbox fully off-screen: nothing to show, the candidate still stands
    meta.update(source=source, nodes=len(nodes), candidates=len(candidates), **{k: ctx[k] for k in
                                                                               ("px_per_dp", "keyboard_top")})
    write_json(os.path.join(out, "meta.json"), meta)
    return 0


def around(platform, device_id, out, action, settle=2.5, ceiling_ms=DEFAULT_CEILING_MS):
    dev = Device(platform, device_id, "")
    os.makedirs(out, exist_ok=True)
    rec = devices.start_recording(dev, os.path.join(out, "video.mp4"))
    time.sleep(0.5)
    t_action = time.time()
    code = subprocess.run(action).returncode
    time.sleep(settle)
    devices.stop_recording(rec)
    record_s = round(time.time() - (rec.started or t_action), 3)
    report = analyze(os.path.join(out, "video.mp4"), out, ceiling_ms, record_s if rec.started else None)
    write_json(os.path.join(out, "around.json"), {"action": action, "action_exit": code, "record_s": record_s,
                                                  "flags": [f["type"] for f in report["flags"]]})
    return 0 if code == 0 else 2


def main():
    argv = sys.argv[1:]
    action = []
    if "--" in argv:
        i = argv.index("--")
        argv, action = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("snap", "around"):
        p = sub.add_parser(name)
        p.add_argument("--platform", required=True, choices=["android", "ios"])
        p.add_argument("--device", required=True)
        p.add_argument("--out", required=True)
    s = sub.choices["snap"]
    s.add_argument("--hierarchy-file"); s.add_argument("--hierarchy-source", choices=["maestro", "uiautomator",
                                                                                     "maestro-cli"])
    s.add_argument("--before"); s.add_argument("--keyboard", action="store_true")
    s.add_argument("--expect-differs", help="pack whose screen must differ (state cells: the default pack)")
    r = sub.choices["around"]
    r.add_argument("--settle", type=float, default=2.5); r.add_argument("--ceiling-ms", type=int,
                                                                        default=DEFAULT_CEILING_MS)
    a = ap.parse_args(argv)
    if a.cmd == "snap":
        code = snap(a.platform, a.device, a.out, a.hierarchy_file, a.hierarchy_source, a.before, a.keyboard,
                    a.expect_differs)
    else:
        if not action:
            raise FEError("around needs an action command after --")
        code = around(a.platform, a.device, a.out, action, a.settle, a.ceiling_ms)
    print(json.dumps({"exit": code, "pack": a.out}))
    return code


if __name__ == "__main__":
    fe_common.cli(main)
