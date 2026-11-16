#!/usr/bin/env python3
"""Fast, deterministic touch probes (a model round-trip is far too slow for a double-tap race).
Every probe names a coverage cell; mutating cells are refused unless preflight allowed mutation.

CLI:
  taps.py rapid   --platform P --device ID --x X --y Y [--count 3] [--interval-ms 120] [--hold-ms 6000]
                  --coverage C --cell ID      (returns --hold-ms after the first tap: a late sheet is on the video)
  taps.py tap     --platform P --device ID (--id SUFFIX | --label RE | --text RE | --som N
                  | --x X --y Y --expect-id SUFFIX) [--index N] (--pack PACK | --after DIR) [--record]
                  --coverage C --cell ID
                  (one tap on the node where a dump taken right before shows it: refused when it is not there, off
                  screen or under the keyboard; --x --y must lie in that node. --som N: mark N of PACK (or of DIR's
                  parent), found again by its class, id, text and label. Once the screen settled, the snap of where
                  the tap led and tap.json (the point, the node and its bounds at the tap) go to DIR, or with
                  --pack to PACK/tap-<k>, k being the row this tap appends to PACK/taps.json: label, changed (nodes
                  or pixels before vs after), result: what appeared, went, took focus). --record: the tap and
                  2 s after the screen settled are recorded there too (capture.py around), for a toast)
  taps.py outside --platform P --device ID --x X --y Y --coverage C --cell ID
  taps.py back    --platform P --device ID [--count 1] [--interval-ms 0] --coverage C --cell ID
  taps.py tap-back --platform P --device ID --x X --y Y [--delay-ms 100] --coverage C --cell ID
  taps.py slow      --platform P --device ID --from X,Y --to X,Y [--ms 2000] --pack PACK --coverage C --cell ID
                    (--to: where a drag across the whole surface ends; drags 40 % of it, 1.5 s later 60 %)
  taps.py drag-back --platform android --device ID --from X,Y --to X,Y [--ms 2000] [--back-at-ms 700]
                    --pack PACK --coverage C --cell ID
  taps.py swipe-tap --platform android --device ID --from X,Y --to X,Y [--ms 250] [--delay-ms MS] --x X --y Y
                    --pack PACK --coverage C --cell ID   (--delay-ms: the run's motion_ceiling_ms unless given)
                    (slow, drag-back, swipe-tap: a drag-interrupt step in one call: PACK/<step>/before snapped on
                    the cell's screen (its marker; refused elsewhere), the gesture recorded into PACK/<step>, and
                    1.5 s later the snap of where it left, into PACK/<step> itself)
  taps.py edge    --platform P --device ID --pack P --node nN --side top|bottom|left|right [--dp 2]
                  --coverage C --cell ID      (a tap just outside the node's visible edge, from the pack's nodes)
"""
import argparse
import json
import os
import tempfile
import time

import capture
import devices
import nav
from devices import Device
from fe_common import FEError, cli, heartbeat, image_size, read_json, write_json
from motion import run_ceiling_ms
from nav import _xy
from pixels import changed_fraction

SETTLE_GAP_S = 0.4  # two screenshots this far apart that match: the screen has settled
SETTLE_MAX_S = 3.0
SAME = 0.01  # changed share under which two frames show one screen (a caret, a clock tick)


def guard(coverage_path, cell_id, platform=None):
    try:
        cov = read_json(coverage_path)
    except (OSError, ValueError) as e:
        raise FEError("cannot read coverage %s: %s" % (coverage_path, e))
    cell = next((c for c in cov.get("cells", []) if c["id"] == cell_id), None)
    if cell is None:
        raise FEError("cell %s is not in %s" % (cell_id, coverage_path))
    if platform and cell.get("platform") != platform:
        raise FEError("cell %s is a %s cell, not %s" % (cell_id, cell.get("platform"), platform))
    if cell.get("mutating"):
        pf_path = os.path.join(os.path.dirname(coverage_path), "preflight.json")
        allowed = os.path.exists(pf_path) and read_json(pf_path).get("mutating_allowed") is True
        if not allowed:
            raise FEError("cell %s is mutating and preflight did not allow mutation" % cell_id)
    return cell


def edge_point(bounds, side, dp, px_per_dp):
    """The point dp beyond a node's visible edge on `side`, centred along it: uiautomator cuts bounds to the parent,
    so a tap there tells whether the declared hit area (hitSlop, a 48 dp box) really answers."""
    x, y, w, h = bounds
    off = round(dp * px_per_dp)
    return {"top": (x + w // 2, y - off), "bottom": (x + w // 2, y + h - 1 + off),
            "left": (x - off, y + h // 2), "right": (x + w - 1 + off, y + h // 2)}[side]


def settle(dev, before, gap_s=SETTLE_GAP_S, max_s=SETTLE_MAX_S):
    """After a tap: wait until two screenshots gap_s apart match and differ from `before` (taken before the tap),
    at most max_s. A snap right after a tap caught the old screen or the transition (8 of 21 false BUGs, 0.5.11).
    `changed: false` after max_s: the tap had no visible result."""
    t0, prev, k = time.time(), None, 0
    with tempfile.TemporaryDirectory() as d:
        while True:
            k += 1
            cur = devices.screenshot(dev, os.path.join(d, "%d.png" % k))
            changed = changed_fraction(before, cur) >= SAME
            same = prev is not None and changed_fraction(prev, cur) < SAME
            if (changed and same) or time.time() - t0 >= max_s:
                return {"settled": same, "changed": changed, "ms": round((time.time() - t0) * 1000)}
            prev = cur
            time.sleep(gap_s)


def som_node(pack, n, nodes):
    """The node mark n of pack/som.json stood for, in a fresh dump: same class, id, text and label, the nearest to
    where it was (rows move after a scroll: 0.5.12's false high tapped a mark's old point). An unlabeled one only
    at its old bounds: its neighbours look the same."""
    mark = next((m for m in read_json(os.path.join(pack, "som.json")) if m["n"] == n), None)
    if mark is None:
        raise FEError("no mark %d in %s/som.json" % (n, pack))
    old = next(o for o in read_json(os.path.join(pack, "nodes.json")) if o["id"] == mark["node_ids"][0])
    key = ("cls", "rid", "text", "label")
    same = [o for o in nodes if all(o[k] == old[k] for k in key)
            and (old["rid"] or old["text"] or old["label"] or o["bounds"] == old["bounds"])]
    if not same:
        raise FEError("mark %d (%r) is not on screen now: tap it by --id/--label, or snap the screen again"
                      % (n, nav._name(old)))
    cx, cy = nav.center(old)
    return min(same, key=lambda o: abs(nav.center(o)[0] - cx) + abs(nav.center(o)[1] - cy))


def target(a, nodes, kb_top, screen_wh, som_pack):
    """((x, y), node, by): where the tap lands, on which node, named how."""
    if a.som is not None:
        node, by = som_node(som_pack, a.som, nodes), "som"
    elif a.x is not None:
        node, by = nav.find_node(nodes, rid=a.expect_id, index=a.index), "expect-id"
    else:
        node, by = nav.find_node(nodes, a.text, a.label, a.id, a.index), "id" if a.id else "label" if a.label \
            else "text"
    x, y, w, h = box = nav.visible_box(node, kb_top, screen_wh)
    if a.x is None:
        return nav.center({"bounds": box}), node, by
    if not (x <= a.x < x + w and y <= a.y < y + h):
        raise FEError("(%d, %d) is outside %r, which is at %s now: tap it by --id instead"
                      % (a.x, a.y, nav._name(node), box))
    return (a.x, a.y), node, by


def _names(nodes):
    return {(n["text"] or n["label"])[:40] for n in nodes
            if (n["text"] or n["label"]) and not n["rid"].startswith("com.android.systemui")}


def what_changed(before, after, kb_before, kb_after, pixels):
    """(changed, result) of a tap from the app's nodes before and after it: a typed emoji or a toggled like
    changes a node while under 1 % of the pixels moved (0.5.12 read those `changed: false`)."""
    if after is None:
        return pixels, "no after snap" + (", the screen changed" if pixels else "")
    new, gone = sorted(_names(after) - _names(before)), sorted(_names(before) - _names(after))
    focus = [nav._name(n) for n in after if n["focused"] and not n["rid"].startswith("com.android.systemui")]
    parts = (["new: " + ", ".join(new[:4])] if new else []) + (["gone: " + ", ".join(gone[:4])] if gone else [])
    if focus != [nav._name(n) for n in before if n["focused"] and not n["rid"].startswith("com.android.systemui")]:
        parts.append("focus: " + (", ".join(focus) or "none"))
    if kb_before != kb_after:
        parts.append("keyboard " + ("up" if kb_after else "down"))
    def layout(nodes):  # not the status bar: its clock ticks
        return sorted(str([n[k] for k in ("cls", "text", "label", "rid", "bounds")]) for n in nodes
                      if not n["rid"].startswith("com.android.systemui"))
    if not parts and layout(before) != layout(after):
        parts.append("moved")
    return bool(parts) or pixels, "; ".join(parts) or ("pixels only" if pixels else "no change")


def tap(a, dev, after, som_pack):
    """One tap on a node from a fresh dump, the settled result snapped into `after` with tap.json beside it.
    Returns (snap exit, tap.json)."""
    nodes, source = nav.hierarchy(dev)
    kb_top = devices.keyboard_top(dev, nodes)
    with tempfile.TemporaryDirectory() as d:
        before = devices.screenshot(dev, os.path.join(d, "before.png"))
        screen_wh = image_size(before)
        (x, y), node, by = target(a, nodes, kb_top, screen_wh, som_pack)
        held = {}

        def act():
            devices.tap(dev, x, y)
            held.update(settle(dev, before))
        if a.record:  # a toast or a "Copied" label is gone ~3 s later, when the snap comes
            capture.around(a.platform, a.device, after, ["taps.py", "tap"], settle=RECORD_AFTER_S, call=act)
        else:
            act()
        result = dict(held)
    code = capture.snap(a.platform, a.device, after)
    snapped = os.path.isfile(os.path.join(after, "nodes.json"))
    meta = read_json(os.path.join(after, "meta.json")) if os.path.isfile(os.path.join(after, "meta.json")) else {}
    changed, summary = what_changed(nodes, read_json(os.path.join(after, "nodes.json")) if snapped else None,
                                    kb_top is not None, meta.get("keyboard_up"), result["changed"])
    rec = dict({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "point": [x, y], "by": by,
                "node": {k: node[k] for k in ("cls", "rid", "text", "label")}, "bounds": node["bounds"],
                "keyboard_top": kb_top, "screen_wh": list(screen_wh), "source": source, "snap_exit": code},
               **dict(result, pixels_changed=result["changed"], changed=changed, result=summary))
    write_json(os.path.join(after, "tap.json"), rec)
    return code, rec


RECORD_AFTER_S = 2.0  # --record: the recording runs on this long after the screen settled (a 2 s toast leaves)
GESTURE_SETTLE_S = 1.5  # after a drag-interrupt gesture: the snap shows where it came to rest


def gesture(a, dev, cell, call):
    """Snap PACK/<step>/before, record call() into PACK/<step>, snap PACK/<step>: all in one call, so no step is
    snapped again after the next one ran (0.5.12 profile-trips: three sub-snaps retaken after all gestures)."""
    sub = os.path.join(a.pack, a.cmd)
    code = capture.snap(a.platform, a.device, os.path.join(sub, "before"), marker=tuple(cell.get("marker") or ()))
    if code:
        raise FEError("%s/before: snap exit %d (see its meta.json): the gesture did not run; bring the screen back "
                      "to rest and run it again" % (sub, code))
    capture.around(a.platform, a.device, sub, ["taps.py", a.cmd], settle=GESTURE_SETTLE_S, call=call)
    return capture.snap(a.platform, a.device, sub)


def _part(src, to, f):
    return round(src[0] + (to[0] - src[0]) * f), round(src[1] + (to[1] - src[1]) * f)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("rapid", "tap", "outside", "back", "tap-back", "slow", "drag-back", "swipe-tap", "edge"):
        p = sub.add_parser(name)
        p.add_argument("--platform", required=True, choices=["android", "ios"])
        p.add_argument("--device", required=True)
        p.add_argument("--coverage", required=True)
        p.add_argument("--cell", required=True)
        if name not in ("back", "slow", "drag-back", "edge"):
            p.add_argument("--x", type=int, required=name != "tap")
            p.add_argument("--y", type=int, required=name != "tap")
        if name == "tap":
            for flag in ("--id", "--label", "--text", "--expect-id"):
                p.add_argument(flag)
            p.add_argument("--som", type=int, help="a mark of the som.json in --pack, or in the parent of --after")
            p.add_argument("--index", type=int, default=0)
            p.add_argument("--record", action="store_true", help="record the tap into the result's pack (video, "
                                                                 "motion.json): for a toast or a short-lived label")
            g = p.add_mutually_exclusive_group(required=True)
            g.add_argument("--pack", help="tap-everything: the row goes to PACK/taps.json, the result to tap-<row>")
            g.add_argument("--after", help="the pack the tap's result is snapped into (a recheck)")
        if name in ("rapid", "back"):
            p.add_argument("--count", type=int, default=3 if name == "rapid" else 1)
            # a person's fast taps; 30 ms is the evil race a script alone makes, asked for explicitly
            p.add_argument("--interval-ms", type=int, default=120 if name == "rapid" else 0)
        if name == "rapid":  # a system share sheet came 2.6 s after the taps, past a 2.2 s video (0.5.11)
            p.add_argument("--hold-ms", type=int, default=6000)
        if name in ("tap-back", "swipe-tap"):
            p.add_argument("--delay-ms", type=int, default=100 if name == "tap-back" else None)
        if name in ("slow", "drag-back", "swipe-tap"):
            p.add_argument("--from", dest="src", type=_xy, required=True)
            p.add_argument("--to", type=_xy, required=True)
            p.add_argument("--ms", type=int, default=250 if name == "swipe-tap" else 2000)
            p.add_argument("--pack", required=True, help="the drag-interrupt pack: snaps go to PACK/%s" % name)
        if name == "drag-back":
            p.add_argument("--back-at-ms", type=int, default=700)
        if name == "edge":
            p.add_argument("--pack", required=True, help="the pack whose nodes.json holds the node")
            p.add_argument("--node", required=True, help="node id from that nodes.json, e.g. n12")
            p.add_argument("--side", required=True, choices=["top", "bottom", "left", "right"])
            p.add_argument("--dp", type=float, default=2)
    a = ap.parse_args()
    if a.cmd == "tap":
        named = [f for f, v in (("--id", a.id), ("--label", a.label), ("--text", a.text), ("--som", a.som),
                                ("--x --y", a.x)) if v not in (None, "")]
        if len(named) != 1 or (a.x is None) != (a.y is None):
            ap.error("tap needs one target: --id, --label, --text, --som, or --x --y with --expect-id")
        if a.x is not None and not a.expect_id:
            ap.error("--x --y only with --expect-id: a point from an older screen lands on whatever moved there")
    cell = guard(a.coverage, a.cell, a.platform)
    heartbeat(a.coverage, a.platform, "taps.py %s %s" % (a.cmd, a.cell))
    dev = Device(a.platform, a.device, "")
    if a.cmd == "rapid":
        t0 = time.time()
        devices.rapid_taps(dev, a.x, a.y, a.count, a.interval_ms)
        time.sleep(max(0.0, a.hold_ms / 1000.0 - (time.time() - t0)))
    elif a.cmd == "tap":
        rows_path = os.path.join(a.pack or "", "taps.json")
        rows = read_json(rows_path) if a.pack and os.path.isfile(rows_path) else []
        after = os.path.join(a.pack, "tap-%d" % (len(rows) + 1)) if a.pack else a.after
        code, rec = tap(a, dev, after, a.pack or os.path.dirname(os.path.normpath(a.after)))
        row = dict({"n": len(rows) + 1, "label": nav._name(rec["node"]), "by": rec["by"]},
                   **({"som": a.som} if a.som is not None else {}),
                   pack=os.path.basename(after), changed=rec["changed"], result=rec["result"])
        if a.pack:  # the folder is the row's number, the navigator writes no row (0.5.12: tap-7/8/9 were rows 4-6)
            write_json(rows_path, rows + [row])
        print(json.dumps(dict(row, cell=a.cell, after=after, snap_exit=code), ensure_ascii=False))
        return code
    elif a.cmd == "edge":
        node = next((n for n in read_json(os.path.join(a.pack, "nodes.json")) if n["id"] == a.node), None)
        if node is None:
            raise FEError("no node %s in %s/nodes.json" % (a.node, a.pack))
        ppd = read_json(os.path.join(a.pack, "meta.json")).get("px_per_dp") or 2.625
        x, y = edge_point(node["bounds"], a.side, a.dp, ppd)
        devices.tap(dev, x, y)
        print(json.dumps({"probe": "edge", "cell": a.cell, "x": x, "y": y}))
        return 0
    elif a.cmd == "outside":
        devices.tap(dev, a.x, a.y)
    elif a.cmd == "tap-back":
        devices.tap_then_back(dev, a.x, a.y, a.delay_ms)
    elif a.cmd in ("slow", "drag-back", "swipe-tap"):
        if a.cmd == "slow":  # under half it springs back, over half it moves on
            def call():
                devices.swipe(dev, *a.src, *_part(a.src, a.to, 0.4), a.ms)
                time.sleep(GESTURE_SETTLE_S)
                devices.swipe(dev, *a.src, *_part(a.src, a.to, 0.6), a.ms)
        elif a.cmd == "drag-back":
            def call():
                devices.drag_then_back(dev, *a.src, *a.to, ms=a.ms, back_at_ms=a.back_at_ms)
        else:
            delay = a.delay_ms if a.delay_ms is not None else run_ceiling_ms(os.path.dirname(a.coverage))

            def call():
                devices.swipe_then_tap(dev, *a.src, *a.to, a.x, a.y, ms=a.ms, delay_ms=delay)
        code = gesture(a, dev, cell, call)
        print(json.dumps({"probe": a.cmd, "cell": a.cell, "pack": os.path.join(a.pack, a.cmd), "snap_exit": code}))
        return code
    else:
        devices.back(dev, a.count, a.interval_ms)
    print(json.dumps({"probe": a.cmd, "cell": a.cell, "ok": True}))
    return 0


if __name__ == "__main__":
    cli(main)
