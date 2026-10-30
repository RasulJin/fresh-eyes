#!/usr/bin/env python3
"""Fast, deterministic touch probes (a model round-trip is far too slow for a double-tap race).
Every probe names a coverage cell; mutating cells are refused unless preflight allowed mutation.

CLI:
  taps.py rapid   --platform P --device ID --x X --y Y [--count 3] [--interval-ms 30] --coverage C --cell ID
  taps.py outside --platform P --device ID --x X --y Y --coverage C --cell ID
  taps.py back    --platform P --device ID [--count 1] [--interval-ms 0] --coverage C --cell ID
  taps.py tap-back --platform P --device ID --x X --y Y [--delay-ms 100] --coverage C --cell ID
  taps.py drag-back --platform android --device ID --from X,Y --to X,Y [--ms 2000] [--back-at-ms 700]
                    --coverage C --cell ID
  taps.py swipe-tap --platform android --device ID --from X,Y --to X,Y [--ms 250] [--delay-ms 300] --x X --y Y
                    --coverage C --cell ID
"""
import argparse
import json
import os

import devices
from devices import Device
from fe_common import FEError, cli, read_json
from nav import _xy


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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("rapid", "outside", "back", "tap-back", "drag-back", "swipe-tap"):
        p = sub.add_parser(name)
        p.add_argument("--platform", required=True, choices=["android", "ios"])
        p.add_argument("--device", required=True)
        p.add_argument("--coverage", required=True)
        p.add_argument("--cell", required=True)
        if name not in ("back", "drag-back"):
            p.add_argument("--x", type=int, required=True)
            p.add_argument("--y", type=int, required=True)
        if name in ("rapid", "back"):
            p.add_argument("--count", type=int, default=3 if name == "rapid" else 1)
            p.add_argument("--interval-ms", type=int, default=30 if name == "rapid" else 0)
        if name in ("tap-back", "swipe-tap"):
            p.add_argument("--delay-ms", type=int, default=100 if name == "tap-back" else 300)
        if name in ("drag-back", "swipe-tap"):
            p.add_argument("--from", dest="src", type=_xy, required=True)
            p.add_argument("--to", type=_xy, required=True)
            p.add_argument("--ms", type=int, default=2000 if name == "drag-back" else 250)
        if name == "drag-back":
            p.add_argument("--back-at-ms", type=int, default=700)
    a = ap.parse_args()
    guard(a.coverage, a.cell, a.platform)
    dev = Device(a.platform, a.device, "")
    if a.cmd == "rapid":
        devices.rapid_taps(dev, a.x, a.y, a.count, a.interval_ms)
    elif a.cmd == "outside":
        devices.tap(dev, a.x, a.y)
    elif a.cmd == "tap-back":
        devices.tap_then_back(dev, a.x, a.y, a.delay_ms)
    elif a.cmd == "drag-back":
        devices.drag_then_back(dev, *a.src, *a.to, ms=a.ms, back_at_ms=a.back_at_ms)
    elif a.cmd == "swipe-tap":
        devices.swipe_then_tap(dev, *a.src, *a.to, a.x, a.y, ms=a.ms, delay_ms=a.delay_ms)
    else:
        devices.back(dev, a.count, a.interval_ms)
    print(json.dumps({"probe": a.cmd, "cell": a.cell, "ok": True}))
    return 0


if __name__ == "__main__":
    cli(main)
