#!/usr/bin/env python3
"""Build the evidence pack for one coverage cell, so the navigator never has to look at pixels.

snap:   screen.png, screen_1000.png, nodes.json, hierarchy.txt, candidates.json, som.png (+som.json),
        crops/<check>-<k>.png (native resolution), meta.json (lit_top: the mean gray of the frame's top half,
        or of a sheet when lighter, keyboard aside; app_theme_set: this call's --app-theme, else null;
        conditions: {slow_animations, app_theme, start_page: the selected tabs/options}, what a recheck repeats).
        Exit 3 = blocked (no hierarchy), 4 = screen identical to --expect-differs,
        5 = none of the --marker words on screen (another screen: meta.json blocked); a `<pack>-recheck` snap
        checks the screen's marker by itself when <pack> showed it. meta.json source: uiautomator, maestro (the
        Maestro CLI, when the UI never gets idle) or xctest.
        --package (Android; default: the run's app, another one is refused): meta.json app_running; a dead app
        also gets logcat.txt with its crash.
        --pages: then scrolls the screen's list half a screen at a time, a pack per page in scroll-<k>/, and back
        to the top (meta.json: scroll_pages, scroll_stop, scroll_restored).
around: records the screen around an action command, then motion analysis: video.mp4, motion.json,
        sheet_*.png, around.json (animated: false = no frame moved; conditions.slow_animations). The ceiling is the run's motion_ceiling_ms
        (coverage.json above --out).
logs:   Android only: the app's warnings and errors since the last `logs --clear`, plus crashes, repeats
        collapsed: logcat.txt.

CLI:
  capture.py snap --platform P --device ID --out PACK [--before PACK] [--keyboard] [--expect-differs PACK] [--pages]
                  [--package PKG] [--marker WORD ...] [--app-theme LABEL]
                  (a dark-* or theme-mismatch-* pack, or --app-theme: theme-note.json {ui_night_mode, app_theme})
  capture.py around --platform P --device ID --out PACK [--settle 2.5] [--ceiling-ms MS] [--frame-width PX]
                    -- CMD ...
  capture.py logs --platform android --device ID --out PACK --package PKG [--clear]
  --queue Q on snap, around and logs: also refreshes <run>/<platform>/heartbeat-Q (nav.py idle --queue Q).
  capture.py step --renumber PACK   (step-N folders 1, 2, … in the order they were snapped, steps.json "n" with
                  them: the fix for steps out of order, never deleting one)
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
import coverage
import devices
import fe_common
import nav
from devices import Device
from fe_common import FEError, image_size, read_json, write_json
from geometry import moved_fraction, run_all
from hierarchy import compress
from motion import MOVE_EPS, analyze, extract_frames, keyboard as follow_keyboard, run_ceiling_ms
from pixels import brightness, changed_fraction, corner_candidates, crop, downscale, lit_top, som_overlay

MIN_DP = {"android": 48, "ios": 44}
MAX_CROPS = 12
MAX_MARKS = 60
STATE_MIN_CHANGE = 0.03  # share of the app area a dark/font state must change
CORNER_CROP = 96  # px around a corner, zoomed x2 for the reviewer
GAP_CONTEXT = 160  # px of content above and below a blank band, so its crop shows what it separates
PAGES = 4  # a long screen: the first viewport and up to 4 more
PAGE_STEP = 0.5  # each drag moves half the list: a blank band up to half a viewport lies whole in some frame
RECHECK = re.compile(r"-recheck(?!\w)")  # <pack>-recheck, -recheck-2, and the folders inside them
MAX_ALL_FRAMES = 240  # ponytail: a 60 s screen that never settles has more; raise it if a verifier needs them
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


DERIVED = ("nodes.json", "hierarchy.txt", "candidates.json", "screen_1000.png", "som.png", "som.json",
           "theme-note.json")
THEME_STATES = ("dark-", "theme-mismatch-")  # packs that must prove the phone's and the app's theme


def recheck_marker(out):
    """A `<pack>-recheck` snap must show the screen its pack showed: the screen's marker (coverage.json of the run)
    when that pack has it. () when there is nothing to check."""
    run, key = fe_common.run_dir(out), coverage.pack_key(out)
    m = re.search(r"-recheck(-\d+)?$", out.rstrip(os.sep))  # -recheck-N: a request with "times"
    if not (m and run and key):
        return ()
    markers = next((c["marker"] for c in read_json(os.path.join(run, "coverage.json"))["cells"]
                    if c["screen"] == key[1] and c.get("marker")), ())
    pack = out.rstrip(os.sep)[:m.start()]
    return tuple(markers) if markers and coverage.shows_marker(pack, markers) else ()


def run_app(path, platform):
    """The app under test of the run `path` lies in (preflight.json: config app.<platform>_id); None outside one."""
    run = fe_common.run_dir(path)
    pf = os.path.join(run, "preflight.json") if run else ""
    return ((read_json(pf).get("platforms") or {}).get(platform) or {}).get("app") if os.path.isfile(pf) else None


def snap(platform, device_id, out, before=None, keyboard=False, expect_differs=None, package=None, marker=(),
         app_theme=None):
    dev = Device(platform, device_id, "")
    app = run_app(out, platform)
    if package and app and package != app:  # 0.5.12: a mistyped package read as a crash of the app
        raise FEError("--package %s is not the app under test %s (config app.%s_id)" % (package, app, platform))
    package = package or app
    os.makedirs(out, exist_ok=True)
    marker = marker or recheck_marker(out)
    for name in DERIVED:  # a blocked re-snap must not leave an older screen's files beside its own frame
        if os.path.exists(os.path.join(out, name)):
            os.remove(os.path.join(out, name))
    shutil.rmtree(os.path.join(out, "crops"), ignore_errors=True)
    # Hierarchy first: uiautomator returns once the UI is idle, so the screenshot taken right after shows the
    # same settled screen (screenshot-first caught mid-transition frames that disagreed with the hierarchy).
    try:
        (nodes, source), hierarchy_error = nav.hierarchy(dev), None
    except FEError as e:  # parse, adb and driver failures alike
        nodes, source, hierarchy_error = None, None, e
    shot = devices.screenshot(dev, os.path.join(out, "screen.png"))
    w, h = image_size(shot)
    meta = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "platform": platform, "device": device_id,
            "screen_wh": [w, h], "brightness": brightness(shot)}
    if package and platform == "android":  # every cell passes here: a crash is caught before anyone relaunches
        meta["app_running"] = bool(devices._adb(device_id, "shell", "pidof", package, check=False).strip())
        if not meta["app_running"]:
            logs(platform, device_id, out, package)
    if hierarchy_error is not None:
        meta["blocked"] = fe_common.redact("hierarchy unavailable: %s" % hierarchy_error)  # it may quote the dump
        write_json(os.path.join(out, "meta.json"), meta)
        return 3
    if expect_differs and changed_fraction(shot, os.path.join(expect_differs, "screen.png")) < STATE_MIN_CHANGE:
        meta["blocked"] = "screen same as %s: the state did not apply, wait and snap again" % expect_differs
        write_json(os.path.join(out, "meta.json"), meta)
        return 4
    write_json(os.path.join(out, "nodes.json"), nodes)
    if marker and coverage.shows_marker(out, marker) is False:  # a probe step run on the wrong screen proves nothing
        meta["blocked"] = "wrong screen: marker not found (%s)" % ", ".join(marker)
        write_json(os.path.join(out, "meta.json"), meta)
        return 5
    with open(os.path.join(out, "hierarchy.txt"), "w", encoding="utf-8") as f:
        f.write(compress(nodes))
    kb_top = devices.keyboard_top(dev, nodes)  # recorded on every snap: a `return` snap must show no keyboard
    # every snap with the keyboard up gets the keyboard checks (a scenario step, a tap's result), not only --keyboard
    ctx = {"screen_wh": [w, h], "px_per_dp": devices.px_per_dp(dev), "min_dp": MIN_DP.get(platform, 48),
           "keyboard_top": kb_top, "keyboard": keyboard}
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
    meta.update(source=source, nodes=len(nodes), candidates=len(candidates),
                keyboard_up=kb_top is not None, **{k: ctx[k] for k in ("px_per_dp", "keyboard_top")})
    # the theme the frame shows, keyboard and a sheet's dimming aside; app_theme_set: only what this call set
    meta.update(lit_top=lit_top(shot, [w, h], kb_top, _panels(app_nodes, w, h)), app_theme_set=app_theme)
    # what a recheck must repeat: the verifier copies these into its request, the recheck's own snap shows them
    meta["conditions"] = {"slow_animations": devices.animations_slow(dev), "app_theme": app_theme,
                          "start_page": " · ".join(nav._name(n) for n in app_nodes
                                                   if n.get("selected") and nav._name(n)) or None}
    write_json(os.path.join(out, "meta.json"), meta)
    key = coverage.pack_key(out)
    if app_theme or (key and key[2] == os.path.basename(os.path.normpath(out)) and key[2].startswith(THEME_STATES)):
        write_json(os.path.join(out, "theme-note.json"), {"ui_night_mode": devices.night_mode(dev),
                                                          "app_theme": app_theme})
    if keyboard and os.path.isfile(os.path.join(out, "kb-show", "video.mp4")):
        meta["keyboard_json"] = _keyboard_json(out) or "written"
        write_json(os.path.join(out, "meta.json"), meta)
    return 0


def _panels(nodes, w, h):
    """Sheets: app nodes nearly as wide as the screen that start below its top and reach its bottom."""
    return [n["bounds"] for n in nodes if n["bounds"][2] >= 0.9 * w and 0.1 * h < n["bounds"][1] < 0.9 * h
            and n["bounds"][1] + n["bounds"][3] >= 0.9 * h]


def _keyboard_json(pack):
    """keyboard.json of a keyboard-open pack once its focus video and its snap are there (and again after
    kb-hide): a recheck ran the probe without motion.py --keyboard and had none (0.5.12). The error, if any."""
    try:
        follow_keyboard(pack)
        return None
    except FEError as e:
        return str(e)


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


def _band(bounds, kb_top=None):
    """(cx, low, high) of a page drag: PAGE_STEP of the list's visible height around its middle, above an open
    keyboard (a drag on it types, Gboard glides a word). None: the keyboard leaves under a quarter of the list."""
    x, y, w, h = bounds
    if kb_top is not None and kb_top - y < h:
        if kb_top - y < h // 4:
            return None
        h = kb_top - y
    return x + w // 2, y + int(h * (0.5 + PAGE_STEP / 2)), y + int(h * (0.5 - PAGE_STEP / 2))


def pages(platform, device_id, out, max_pages=PAGES):
    """After a snap into `out`: scroll its main list page by page, a pack each in scroll-<k>/, then the same
    distances back, so the next cell starts at the top. The checks see the whole screen, not its first viewport.
    scroll_stop: "end", "limit" (max_pages reached), "keyboard covers list" or the failed snap."""
    dev = Device(platform, device_id, "")
    meta = read_json(os.path.join(out, "meta.json"))
    nodes = read_json(os.path.join(out, "nodes.json"))
    lst = main_list(nodes, meta["screen_wh"][1])
    band = lst and _band(lst["bounds"], devices.keyboard_top(dev, nodes))
    shifts, stop = [], "end" if band or lst is None else "keyboard covers list"
    if band:
        cx, low, high = band
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


def around(platform, device_id, out, action, settle=2.5, ceiling_ms=None, call=None, frame_width=None):
    """Record the screen around the command `action`, or around `call()` (then `action` only describes it).
    frame_width: the frames' width in px (default 300 px tall, ~136 px wide on a phone)."""
    dev = Device(platform, device_id, "")
    os.makedirs(out, exist_ok=True)
    if ceiling_ms is None:
        ceiling_ms = run_ceiling_ms(out)
    slow = devices.animations_slow(dev)  # a recheck of this recording must run at the same speed
    rec = devices.start_recording(dev, os.path.join(out, "video.mp4"))
    try:
        time.sleep(0.5)
        t_action = time.time()
        code = subprocess.run(action).returncode if call is None else (call() or 0)
        time.sleep(settle)
        record_s = round(time.time() - rec.started, 3)  # before stopping: the pull is not recording
    except BaseException:
        try:  # a recorder left running on the device would hold the next recording's file
            devices.stop_recording(rec)
        except FEError:
            pass
        raise
    devices.stop_recording(rec)
    t0 = round(t_action - rec.started, 3) if platform == "android" else 0.0  # Android: first change ~ the action
    report = analyze(os.path.join(out, "video.mp4"), out, ceiling_ms, record_s, t0, frame_width)
    extra = {}
    if RECHECK.search(out):  # a verifier asked again: every frame, not 8 around each of the first 4 flags
        extra["frames_all"] = len(extract_frames(os.path.join(out, "video.mp4"), os.path.join(out, "frames_all"),
                                                 max_frames=MAX_ALL_FRAMES, width=frame_width))
    parent = os.path.dirname(os.path.normpath(out))
    if os.path.basename(os.path.normpath(out)) == "kb-hide" and os.path.isfile(os.path.join(parent, "nodes.json")):
        extra["keyboard_json"] = _keyboard_json(parent) or "written"
    write_json(os.path.join(out, "around.json"), dict({"action": action, "action_exit": code, "record_s": record_s,
                                                       "flags": [f["type"] for f in report["flags"]],
                                                       "animated": report["peak_change"] >= MOVE_EPS,
                                                       "conditions": {"slow_animations": slow}}, **extra))
    return 0 if code == 0 else 2


STEP = re.compile(r"step-(\d+)")


def _step_time(folder):
    """When the step was snapped: its meta.json time, else its before/ snap's, else the folder's mtime."""
    for meta in (os.path.join(folder, "meta.json"), os.path.join(folder, "before", "meta.json")):
        if os.path.isfile(meta) and read_json(meta).get("time"):
            return read_json(meta)["time"]
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(os.path.getmtime(folder)))


def renumber(pack):
    """step-N folders numbered 1, 2, … in the order they were snapped, steps.json's "n" moved with them (a step
    there without a folder stays after the one it followed). Fixes an order coverage.py refuses without deleting
    a step (0.5.13 quick: the navigator removed step-10 and step-13). Returns {old: new} of what moved."""
    folders = {int(m.group(1)): d for d in os.listdir(pack) for m in [STEP.fullmatch(d)]
               if m and os.path.isdir(os.path.join(pack, d))}
    path = os.path.join(pack, "steps.json")
    steps = read_json(path) if os.path.isfile(path) else []
    loose, lead, cur = {}, [], None  # steps.json entries without a folder, after the folder step they followed
    for n in sorted(set(folders) | {s["n"] for s in steps if isinstance(s.get("n"), int)}):
        if n in folders:
            cur = n
        else:
            (loose.setdefault(cur, []) if cur is not None else lead).append(n)
    order = lead + [m for f in sorted(folders, key=lambda f: (_step_time(os.path.join(pack, folders[f])), f))
                    for m in [f] + loose.get(f, [])]
    moved = {old: k + 1 for k, old in enumerate(order) if old != k + 1}
    for old in moved:
        if old in folders:
            os.rename(os.path.join(pack, folders[old]), os.path.join(pack, ".renumber-%d" % old))
    for old, new in moved.items():
        if old in folders:
            os.rename(os.path.join(pack, ".renumber-%d" % old), os.path.join(pack, "step-%d" % new))
    if steps and moved:
        write_json(path, sorted([dict(s, n=moved.get(s.get("n"), s.get("n"))) for s in steps],
                                key=lambda s: s["n"] if isinstance(s.get("n"), int) else 0))
    return moved


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
    sub.add_parser("step").add_argument("--renumber", metavar="PACK", required=True,
                                        help="number PACK's step-N folders in snap order, steps.json with them")
    for name in ("snap", "around", "logs"):
        p = sub.add_parser(name)
        p.add_argument("--platform", required=True, choices=["android", "ios"])
        p.add_argument("--device", required=True)
        p.add_argument("--out", required=True)
        p.add_argument("--queue", type=fe_common.queue_name, help="also <run>/<platform>/heartbeat-<queue>")
    s = sub.choices["snap"]
    s.add_argument("--before"); s.add_argument("--keyboard", action="store_true")
    s.add_argument("--expect-differs", help="pack whose screen must differ (state cells: the default pack)")
    s.add_argument("--pages", action="store_true", help="then a pack per scrolled page in scroll-<k>/")
    s.add_argument("--package", help="Android app id: records app_running, a dead app's crash goes to logcat.txt")
    s.add_argument("--marker", nargs="+", default=(), help="the screen's marker words (coverage.json cell)")
    s.add_argument("--app-theme", help="dark / theme-mismatch: the app's own theme option now picked (its label, "
                                       "or system): theme-note.json")
    r = sub.choices["around"]
    r.usage = ("capture.py around --platform P --device ID --out PACK [--settle S] [--ceiling-ms MS] "
               "[--frame-width PX] -- CMD ...")
    r.add_argument("--settle", type=float, default=2.5)
    r.add_argument("--ceiling-ms", type=int, help="overrides the run's motion_ceiling_ms (coverage.json)")
    r.add_argument("--frame-width", type=int, help="frames this many px wide (a recheck that must read them)")
    g = sub.choices["logs"]
    g.add_argument("--package", required=True); g.add_argument("--clear", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "step":
        print(json.dumps({"pack": a.renumber, "renumbered": {"step-%d" % k: "step-%d" % v
                                                             for k, v in renumber(a.renumber).items()}}))
        return 0
    fe_common.load_secrets(a.out)  # the config above the run: its credentials never reach a pack
    fe_common.heartbeat(a.out, a.platform, "capture.py %s %s" % (a.cmd, a.out), a.queue)
    out = {}
    if a.cmd == "snap":
        code = snap(a.platform, a.device, a.out, before=a.before, keyboard=a.keyboard,
                    expect_differs=a.expect_differs, package=a.package, marker=a.marker, app_theme=a.app_theme)
        out = {k: v for k, v in read_json(os.path.join(a.out, "meta.json")).items() if k == "app_running"}
        if code == 0 and a.pages:
            m = pages(a.platform, a.device, a.out)
            out.update((k, m[k]) for k in ("scroll_pages", "scroll_stop", "scroll_restored"))
    elif a.cmd == "logs":
        code = logs(a.platform, a.device, a.out, a.package, a.clear)
    else:
        if not action:
            raise FEError("around needs an action command after --")
        code = around(a.platform, a.device, a.out, action, a.settle, a.ceiling_ms, frame_width=a.frame_width)
    print(json.dumps(dict({"exit": code, "pack": a.out}, **out)))
    return code


if __name__ == "__main__":
    fe_common.cli(main)
