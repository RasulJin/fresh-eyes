#!/usr/bin/env python3
"""Fast, deterministic touch probes (a model round-trip is far too slow for a double-tap race).
Every probe names a coverage cell; mutating cells are refused unless preflight allowed mutation.

CLI:
  taps.py rapid   --platform P --device ID --x X --y Y [--count 3] [--interval-ms 120] [--hold-ms 6000]
                  --coverage C --cell ID      (returns --hold-ms after the first tap: a late sheet is on the video)
  taps.py tap     --platform P --device ID --x X --y Y --coverage C --cell ID
                  (one tap; returns once the screen settled, so the snap after it shows where the tap led)
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

import devices
from devices import Device
from fe_common import FEError, cli, heartbeat, read_json
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
            p.add_argument("--x", type=int, required=True)
            p.add_argument("--y", type=int, required=True)
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
    guard(a.coverage, a.cell, a.platform)
    heartbeat(a.coverage, a.platform, "taps.py %s %s" % (a.cmd, a.cell))
    dev = Device(a.platform, a.device, "")
    if a.cmd == "rapid":
        t0 = time.time()
        devices.rapid_taps(dev, a.x, a.y, a.count, a.interval_ms)
        time.sleep(max(0.0, a.hold_ms / 1000.0 - (time.time() - t0)))
    elif a.cmd == "tap":
        with tempfile.TemporaryDirectory() as d:
            before = devices.screenshot(dev, os.path.join(d, "before.png"))
            devices.tap(dev, a.x, a.y)
            print(json.dumps(dict({"probe": "tap", "cell": a.cell, "x": a.x, "y": a.y}, **settle(dev, before))))
        return 0
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
