#!/usr/bin/env python3
"""Fast, deterministic touch probes (a model round-trip is far too slow for a double-tap race).
Every probe names a coverage cell; mutating cells are refused unless preflight allowed mutation.

CLI:
  taps.py rapid   --platform P --device ID --x X --y Y [--count 3] [--interval-ms 120] [--hold-ms 6000]
                  --coverage C --cell ID      (returns --hold-ms after the first tap: a late sheet is on the video)
  taps.py tap     --platform P --device ID (--id SUFFIX | --label RE | --text RE | --som N
                  | --x X --y Y --expect-id SUFFIX) [--index N] --after DIR --coverage C --cell ID
                  (one tap on the node where a dump taken right before shows it: refused when it is not there, off
                  screen or under the keyboard; --x --y must lie in that node. --som N: mark N of DIR's parent
                  pack, found again by its class, id, text and label. Once the screen settled, DIR gets the snap of
                  where the tap led and tap.json: the point, the node and its bounds at the tap)
  taps.py outside --platform P --device ID --x X --y Y --coverage C --cell ID
  taps.py back    --platform P --device ID [--count 1] [--interval-ms 0] --coverage C --cell ID
  taps.py tap-back --platform P --device ID --x X --y Y [--delay-ms 100] --coverage C --cell ID
  taps.py drag-back --platform android --device ID --from X,Y --to X,Y [--ms 2000] [--back-at-ms 700]
                    --coverage C --cell ID
  taps.py swipe-tap --platform android --device ID --from X,Y --to X,Y [--ms 250] [--delay-ms MS] --x X --y Y
                    --coverage C --cell ID      (--delay-ms: the run's motion_ceiling_ms unless given)
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


def tap(a, dev, after, som_pack):
    """One tap on a node from a fresh dump, the settled result snapped into `after` with tap.json beside it."""
    nodes, source = nav.hierarchy(dev)
    kb_top = devices.keyboard_top(dev, nodes)
    with tempfile.TemporaryDirectory() as d:
        before = devices.screenshot(dev, os.path.join(d, "before.png"))
        screen_wh = image_size(before)
        (x, y), node, by = target(a, nodes, kb_top, screen_wh, som_pack)
        devices.tap(dev, x, y)
        result = settle(dev, before)
    code = capture.snap(a.platform, a.device, after)
    write_json(os.path.join(after, "tap.json"), dict(
        {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "point": [x, y], "by": by,
         "node": {k: node[k] for k in ("cls", "rid", "text", "label")}, "bounds": node["bounds"],
         "keyboard_top": kb_top, "screen_wh": list(screen_wh), "source": source, "snap_exit": code}, **result))
    return code, nodes, node, result


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("rapid", "tap", "outside", "back", "tap-back", "drag-back", "swipe-tap", "edge"):
        p = sub.add_parser(name)
        p.add_argument("--platform", required=True, choices=["android", "ios"])
        p.add_argument("--device", required=True)
        p.add_argument("--coverage", required=True)
        p.add_argument("--cell", required=True)
        if name not in ("back", "drag-back", "edge"):
            p.add_argument("--x", type=int, required=name != "tap")
            p.add_argument("--y", type=int, required=name != "tap")
        if name == "tap":
            for flag in ("--id", "--label", "--text", "--expect-id"):
                p.add_argument(flag)
            p.add_argument("--som", type=int, help="a mark of the som.json in the parent of --after")
            p.add_argument("--index", type=int, default=0)
            p.add_argument("--after", required=True, help="the pack the tap's result is snapped into")
        if name in ("rapid", "back"):
            p.add_argument("--count", type=int, default=3 if name == "rapid" else 1)
            # a person's fast taps; 30 ms is the evil race a script alone makes, asked for explicitly
            p.add_argument("--interval-ms", type=int, default=120 if name == "rapid" else 0)
        if name == "rapid":  # a system share sheet came 2.6 s after the taps, past a 2.2 s video (0.5.11)
            p.add_argument("--hold-ms", type=int, default=6000)
        if name in ("tap-back", "swipe-tap"):
            p.add_argument("--delay-ms", type=int, default=100 if name == "tap-back" else None)
        if name in ("drag-back", "swipe-tap"):
            p.add_argument("--from", dest="src", type=_xy, required=True)
            p.add_argument("--to", type=_xy, required=True)
            p.add_argument("--ms", type=int, default=2000 if name == "drag-back" else 250)
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
    guard(a.coverage, a.cell, a.platform)
    heartbeat(a.coverage, a.platform, "taps.py %s %s" % (a.cmd, a.cell))
    dev = Device(a.platform, a.device, "")
    if a.cmd == "rapid":
        t0 = time.time()
        devices.rapid_taps(dev, a.x, a.y, a.count, a.interval_ms)
        time.sleep(max(0.0, a.hold_ms / 1000.0 - (time.time() - t0)))
    elif a.cmd == "tap":
        code, _, node, result = tap(a, dev, a.after, os.path.dirname(os.path.normpath(a.after)))
        print(json.dumps(dict({"probe": "tap", "cell": a.cell, "node": nav._name(node), "after": a.after,
                               "snap_exit": code}, **result), ensure_ascii=False))
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
    elif a.cmd == "drag-back":
        devices.drag_then_back(dev, *a.src, *a.to, ms=a.ms, back_at_ms=a.back_at_ms)
    elif a.cmd == "swipe-tap":
        delay = a.delay_ms if a.delay_ms is not None else run_ceiling_ms(os.path.dirname(a.coverage))
        devices.swipe_then_tap(dev, *a.src, *a.to, a.x, a.y, ms=a.ms, delay_ms=delay)
    else:
        devices.back(dev, a.count, a.interval_ms)
    print(json.dumps({"probe": a.cmd, "cell": a.cell, "ok": True}))
    return 0


if __name__ == "__main__":
    cli(main)
