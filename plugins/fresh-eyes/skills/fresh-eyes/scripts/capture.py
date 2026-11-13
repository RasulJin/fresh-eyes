#!/usr/bin/env python3
"""Build the evidence pack for one coverage cell, so the navigator never has to look at pixels.

snap:   screen.png, screen_1000.png, nodes.json, hierarchy.txt, candidates.json, som.png (+som.json),
        crops/<check>-<k>.png (native resolution), meta.json. Exit 3 = blocked (no hierarchy), 4 = screen identical to --expect-differs.
        --package (Android): meta.json app_running; a dead app also gets logcat.txt with its crash.
        --pages: then scrolls the screen's list half a screen at a time, a pack per page in scroll-<k>/, and back
        to the top (meta.json: scroll_pages, scroll_stop, scroll_restored).
around: records the screen around an action command, then motion analysis: video.mp4, motion.json,
        sheet_*.png, around.json. The ceiling is the run's motion_ceiling_ms (coverage.json above --out).
logs:   Android only: the app's warnings and errors since the last `logs --clear`, plus crashes, repeats
        collapsed: logcat.txt.

CLI:
  capture.py snap --platform P --device ID --out PACK [--before PACK] [--keyboard] [--expect-differs PACK] [--pages]
                  [--package PKG]
  capture.py around --platform P --device ID --out PACK [--settle 2.5] [--ceiling-ms MS] -- CMD ...
  capture.py logs --platform android --device ID --out PACK --package PKG [--clear]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict

import composition
import devices
import fe_common
import nav
from devices import Device
from fe_common import FEError, image_size, read_json, write_json
from geometry import moved_fraction, run_all
from hierarchy import compress
from motion import analyze, run_ceiling_ms
from pixels import brightness, changed_fraction, corner_candidates, crop, downscale, som_overlay

MIN_DP = {"android": 48, "ios": 44}
MAX_CROPS = 12
MAX_MARKS = 60
STATE_MIN_CHANGE = 0.03  # share of the app area a dark/font state must change
CORNER_CROP = 96  # px around a corner, zoomed x2 for the reviewer
GAP_CONTEXT = 160  # px of content above and below a blank band, so its crop shows what it separates
PAGES = 4  # a long screen: the first viewport and up to 4 more
PAGE_STEP = 0.5  # each drag moves half the list: a blank band up to half a viewport lies whole in some frame
PAGE_MS = 800  # a slow drag, so the list stops where the finger does
TOUCH_SLOP_DP = 8  # the drag a list eats before it starts to move


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


def _crop_order(candidates):
    """Round-robin over checks, most severe first, so a flood of one kind cannot take every crop."""
    rank = {"high": 0, "medium": 1, "low": 2}
    groups = {}
    for c in sorted(candidates, key=lambda c: rank.get(c["hint"], 3)):
        groups.setdefault(c["check"], []).append(c)
    order, queues = [], list(groups.values())
    while queues:
        order += [q.pop(0) for q in queues]
        queues = [q for q in queues if q]
    return order


def snap(platform, device_id, out, before=None, keyboard=False, expect_differs=None, package=None):
    dev = Device(platform, device_id, "")
    os.makedirs(out, exist_ok=True)
    # Hierarchy first: uiautomator returns once the UI is idle, so the screenshot taken right after shows the
    # same settled screen (screenshot-first caught mid-transition frames that disagreed with the hierarchy).
    try:
        nodes, hierarchy_error = nav.screen_nodes(dev), None
    except FEError as e:  # parse, adb and driver failures alike
        nodes, hierarchy_error = None, e
    shot = devices.screenshot(dev, os.path.join(out, "screen.png"))
    w, h = image_size(shot)
    meta = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "platform": platform, "device": device_id,
            "screen_wh": [w, h], "brightness": brightness(shot)}
    if package and platform == "android":  # every cell passes here: a crash is caught before anyone relaunches
        meta["app_running"] = bool(devices._adb(device_id, "shell", "pidof", package, check=False).strip())
        if not meta["app_running"]:
            logs(platform, device_id, out, package)
    if hierarchy_error is not None:
        meta["blocked"] = "hierarchy unavailable: %s" % hierarchy_error
        write_json(os.path.join(out, "meta.json"), meta)
        return 3
    if expect_differs and changed_fraction(shot, os.path.join(expect_differs, "screen.png")) < STATE_MIN_CHANGE:
        meta["blocked"] = "screen same as %s: the state did not apply, wait and snap again" % expect_differs
        write_json(os.path.join(out, "meta.json"), meta)
        return 4
    write_json(os.path.join(out, "nodes.json"), nodes)
    with open(os.path.join(out, "hierarchy.txt"), "w", encoding="utf-8") as f:
        f.write(compress(nodes))
    kb_top = devices.keyboard_top(dev, nodes)  # recorded on every snap: a `return` snap must show no keyboard
    ctx = {"screen_wh": [w, h], "px_per_dp": devices.px_per_dp(dev), "min_dp": MIN_DP.get(platform, 48),
           "keyboard_top": kb_top if keyboard else None, "keyboard": keyboard}
    before_nodes = read_json(os.path.join(before, "nodes.json")) if before else None
    candidates = run_all(nodes, ctx, before_nodes)
    app_nodes = [n for n in nodes if not n["rid"].startswith("com.android.systemui")]
    candidates += corner_candidates(shot, app_nodes, [w, h])
    candidates += composition.run_all(shot, app_nodes, ctx["px_per_dp"] or 2.625, kb_top)
    write_json(os.path.join(out, "candidates.json"), candidates)
    downscale(shot, os.path.join(out, "screen_1000.png"))
    full_som = os.path.join(out, ".som_full.png")
    mapping = som_overlay(shot, _marks(nodes, candidates), full_som)
    downscale(full_som, os.path.join(out, "som.png"))
    os.remove(full_som)
    write_json(os.path.join(out, "som.json"), mapping)
    shutil.rmtree(os.path.join(out, "crops"), ignore_errors=True)  # a re-snap must not keep crops of an old screen
    counters = {}
    for c in _crop_order(candidates)[:MAX_CROPS]:
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
                crop(shot, os.path.join(out, "crops", "%s-%d.png" % (c["check"], k)), c["bbox"],
                     pad=GAP_CONTEXT if c["check"] == "blank_gap" else 8)
        except FEError:
            continue  # bbox fully off-screen: nothing to show, the candidate still stands
    if before:
        meta.update(before=before, moved=moved_fraction(before_nodes, nodes))
    meta.update(source="xctest" if platform == "ios" else "uiautomator", nodes=len(nodes), candidates=len(candidates),
                keyboard_up=kb_top is not None, **{k: ctx[k] for k in ("px_per_dp", "keyboard_top")})
    write_json(os.path.join(out, "meta.json"), meta)
    return 0


def main_list(nodes, screen_h):
    """The screen's vertical list: the innermost scroll container at least half the screen tall."""
    lists = [n for n in nodes if n["cls"].endswith(composition.SCROLLS) and "Horizontal" not in n["cls"]
             and n["bounds"][3] >= screen_h / 2]
    return max(lists, key=lambda n: n["depth"], default=None)


def _shift(a, b, box):
    """How far the list moved up from snap a to snap b, in px: the y change of texts wholly inside the list
    (a sticky header stays put and is not counted). None: no text in both to measure by; 0: none moved."""
    def tops(nodes):
        inside = [n for n in nodes if (n["text"] or n["label"]) and n["bounds"][1] > box[1]
                  and n["bounds"][1] + n["bounds"][3] < box[1] + box[3]]
        seen = Counter(n["text"] or n["label"] for n in inside)
        return {n["text"] or n["label"]: n["bounds"][1] for n in inside if seen[n["text"] or n["label"]] == 1}
    ta, tb = tops(a), tops(b)
    common = [k for k in ta if k in tb]
    moved = Counter(ta[k] - tb[k] for k in common if ta[k] != tb[k])
    return moved.most_common(1)[0][0] if moved else (0 if common else None)


def pages(platform, device_id, out, max_pages=PAGES):
    """After a snap into `out`: scroll its main list page by page, a pack each in scroll-<k>/, then the same
    distances back, so the next cell starts at the top. The checks see the whole screen, not its first viewport.
    scroll_stop: "end", "limit" (max_pages reached) or the failed snap."""
    dev = Device(platform, device_id, "")
    meta = read_json(os.path.join(out, "meta.json"))
    lst = main_list(read_json(os.path.join(out, "nodes.json")), meta["screen_wh"][1])
    shifts, stop = [], "end"
    if lst is not None:
        x, y, w, h = lst["bounds"]
        cx, low, high = x + w // 2, y + int(h * (0.5 + PAGE_STEP / 2)), y + int(h * (0.5 - PAGE_STEP / 2))
        slop = round(TOUCH_SLOP_DP * (meta.get("px_per_dp") or 2.625))
        prev = out
        for k in range(1, max_pages + 1):
            devices.swipe(dev, cx, low, cx, high, PAGE_MS)
            time.sleep(nav.SETTLE_S)
            page = os.path.join(out, "scroll-%d" % k)
            code, moved = snap(platform, device_id, page), 0
            if code:
                stop = "snap failed on page %d (exit %d)" % (k, code)
            elif changed_fraction(os.path.join(page, "screen.png"), os.path.join(prev, "screen.png")) >= 0.01:
                moved = _shift(read_json(os.path.join(prev, "nodes.json")),
                               read_json(os.path.join(page, "nodes.json")), lst["bounds"])  # 0: only an animation
            if moved == 0:  # the end of the list, or the snap failed
                shutil.rmtree(page, ignore_errors=True)
                break
            shifts.append(low - high - slop if moved is None else moved)  # None: no text to measure, a full step
            prev = page
        else:
            stop = "limit"
        for moved in reversed(shifts):  # a longer drag back would overscroll the top into pull-to-refresh
            devices.swipe(dev, cx, high, cx, high + moved + slop, PAGE_MS)
        time.sleep(nav.SETTLE_S)
    top = devices.screenshot(dev, os.path.join(out, ".top.png"))
    meta.update(scroll_pages=len(shifts), scroll_stop=stop,
                scroll_restored=changed_fraction(top, os.path.join(out, "screen.png")) < STATE_MIN_CHANGE)
    os.remove(top)
    write_json(os.path.join(out, "meta.json"), meta)
    return meta


def around(platform, device_id, out, action, settle=2.5, ceiling_ms=None):
    dev = Device(platform, device_id, "")
    os.makedirs(out, exist_ok=True)
    if ceiling_ms is None:
        ceiling_ms = run_ceiling_ms(out)
    rec = devices.start_recording(dev, os.path.join(out, "video.mp4"))
    time.sleep(0.5)
    t_action = time.time()
    code = subprocess.run(action).returncode
    time.sleep(settle)
    record_s = round(time.time() - rec.started, 3)  # before stopping: the pull is not recording
    devices.stop_recording(rec)
    t0 = round(t_action - rec.started, 3) if platform == "android" else 0.0  # Android: first change ~ the action
    report = analyze(os.path.join(out, "video.mp4"), out, ceiling_ms, record_s, t0)
    write_json(os.path.join(out, "around.json"), {"action": action, "action_exit": code, "record_s": record_s,
                                                  "flags": [f["type"] for f in report["flags"]]})
    return 0 if code == 0 else 2


CRASH_TAGS = ("AndroidRuntime", "DEBUG", "libc")  # a crash is logged by the system, not by the app's own pid
BUFFERS = ("-b", "main", "-b", "system", "-b", "crash")  # the crash buffer is not a default on every image
_BRIEF = re.compile(r"([WEF])/([^(]+)\(\s*(\d+)\): (.*)")


def logs(platform, device_id, out, package, clear=False):
    if platform != "android":
        raise FEError("logs: Android only (iOS app logs are not automated yet)")
    if clear:
        devices._adb(device_id, "logcat", *BUFFERS, "-c")
        return 0
    pid = devices._adb(device_id, "shell", "pidof", package, check=False).strip()
    seen, first, crashes = Counter(), {}, defaultdict(list)

    def add(level, tag, msg):
        key = "%s/%s: %s" % (level, tag, re.sub(r"\d+", "#", msg)[:300])
        seen[key] += 1  # a warning flood (thousands of the same line with other ids) is one line
        first.setdefault(key, "%s/%s: %s" % (level, tag, msg[:300]))
    for line in devices._adb(device_id, "logcat", *BUFFERS, "-d", "-v", "brief", "*:W", timeout=60).splitlines():
        m = _BRIEF.match(line)
        if not m:
            continue
        level, tag, line_pid, msg = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
        if tag in CRASH_TAGS:
            crashes[(tag, line_pid)].append((level, tag, msg))
        elif line_pid == pid or level == "F":  # a fatal line of a dead app has no pid to match
            add(level, tag, msg)
    for block in crashes.values():  # the crashed process is gone, so its block is kept when it names the app
        if any(package in msg for _, _, msg in block):
            for level, tag, msg in block:
                add(level, tag, msg)
    order = sorted(seen, key=lambda k: ("FEW".index(k[0]), k[2:].split(":")[0] not in CRASH_TAGS, -seen[k]))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "logcat.txt"), "w", encoding="utf-8") as f:
        f.write("# %s pid %s: %d warnings/errors, %d distinct\n" % (package, pid or "not running",
                                                                     sum(seen.values()), len(seen)))
        f.writelines("%6d x %s\n" % (seen[k], first[k]) for k in order[:200])
    return 0


def main():
    argv = sys.argv[1:]
    action = []
    if "--" in argv:
        i = argv.index("--")
        argv, action = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("snap", "around", "logs"):
        p = sub.add_parser(name)
        p.add_argument("--platform", required=True, choices=["android", "ios"])
        p.add_argument("--device", required=True)
        p.add_argument("--out", required=True)
    s = sub.choices["snap"]
    s.add_argument("--before"); s.add_argument("--keyboard", action="store_true")
    s.add_argument("--expect-differs", help="pack whose screen must differ (state cells: the default pack)")
    s.add_argument("--pages", action="store_true", help="then a pack per scrolled page in scroll-<k>/")
    s.add_argument("--package", help="Android app id: records app_running, a dead app's crash goes to logcat.txt")
    r = sub.choices["around"]
    r.usage = "capture.py around --platform P --device ID --out PACK [--settle S] [--ceiling-ms MS] -- CMD ..."
    r.add_argument("--settle", type=float, default=2.5)
    r.add_argument("--ceiling-ms", type=int, help="overrides the run's motion_ceiling_ms (coverage.json)")
    g = sub.choices["logs"]
    g.add_argument("--package", required=True); g.add_argument("--clear", action="store_true")
    a = ap.parse_args(argv)
    out = {}
    if a.cmd == "snap":
        code = snap(a.platform, a.device, a.out, before=a.before, keyboard=a.keyboard,
                    expect_differs=a.expect_differs, package=a.package)
        out = {k: v for k, v in read_json(os.path.join(a.out, "meta.json")).items() if k == "app_running"}
        if code == 0 and a.pages:
            m = pages(a.platform, a.device, a.out)
            out.update((k, m[k]) for k in ("scroll_pages", "scroll_stop", "scroll_restored"))
    elif a.cmd == "logs":
        code = logs(a.platform, a.device, a.out, a.package, a.clear)
    else:
        if not action:
            raise FEError("around needs an action command after --")
        code = around(a.platform, a.device, a.out, action, a.settle, a.ceiling_ms)
    print(json.dumps(dict({"exit": code, "pack": a.out}, **out)))
    return code


if __name__ == "__main__":
    fe_common.cli(main)
