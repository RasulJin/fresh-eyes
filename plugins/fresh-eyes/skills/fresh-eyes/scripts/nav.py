#!/usr/bin/env python3
"""Navigation by the view hierarchy, printed as compact text (~1.5k chars a screen instead of ~15k of MCP JSON).

Android: uiautomator + adb. Only one process may hold UiAutomation, and the Maestro MCP driver holds it, so the
MCP is never used on Android. iOS: the HTTP API of the Maestro XCTest driver that the MCP started (one MCP
inspect_screen on the simulator starts it). Coordinates are screenshot pixels on both platforms. Matching follows
Maestro: text/label are full-string, case-insensitive regexes; ids match by suffix.

CLI (all print the compressed screen after acting; --platform defaults to android):
  nav.py screen --device ID [--platform android|ios]
  nav.py tap    --device ID (--text RE | --label RE | --id SUFFIX | --point X,Y) [--index N]
  nav.py type   --device ID --text "text"        (Android: ASCII only)
  nav.py swipe  --device ID --from X,Y --to X,Y [--ms 300]
  nav.py back   --device ID                      (iOS: edge swipe)
  nav.py wait   --device ID (--text RE | --label RE | --id SUFFIX) [--timeout 15]
"""
import argparse
import re
import sys
import time

import devices
import fe_common
from devices import Device
from fe_common import FEError, cli
from hierarchy import compress, normalize

SETTLE_S = 1.0


def _dump_uiautomator(dev):
    return fe_common.run([devices.adb_path(), "-s", dev.id, "exec-out", "uiautomator", "dump", "/dev/tty"],
                         timeout=40).stdout.decode("utf-8", "replace")


def screen_nodes(dev):
    if dev.platform == "ios":
        base = devices.ios_driver(dev)
        raw = devices.ios_call(dev, "viewHierarchy", {"appIds": [], "excludeKeyboardElements": False}, base=base)
        return normalize(raw, "xctest", devices.ios_scale(dev, base))
    last = None
    for _ in range(3):
        try:
            return normalize(_dump_uiautomator(dev), "uiautomator")
        except FEError as e:
            last = e
            time.sleep(0.7)
    raise FEError("no hierarchy (is the Maestro driver holding UiAutomation? %s)" % last)


def _full(pattern, value):
    return bool(value) and re.fullmatch(pattern, value, flags=re.I | re.S) is not None


def find_node(nodes, text=None, label=None, rid=None, index=0):
    matches = [n for n in nodes
               if (text is None or _full(text, n["text"]))
               and (label is None or _full(label, n["label"]))
               and (rid is None or n["rid"].endswith(rid))]
    if len(matches) <= index:
        visible = sorted({n["text"] or n["label"] for n in nodes if n["text"] or n["label"]})[:40]
        raise FEError("no element %s (index %d). Visible: %s" % (
            {k: v for k, v in (("text", text), ("label", label), ("id", rid)) if v}, index, "; ".join(visible)))
    return matches[index]


def center(node):
    x, y, w, h = node["bounds"]
    return x + w // 2, y + h // 2


def tap(dev, text=None, label=None, rid=None, point=None, index=0):
    if point:
        devices.tap(dev, point[0], point[1])
        time.sleep(SETTLE_S)
        return {"id": None, "text": "", "bounds": [point[0], point[1], 0, 0]}
    node = find_node(screen_nodes(dev), text, label, rid, index)
    devices.tap(dev, *center(node))
    time.sleep(SETTLE_S)
    return node


def adb_text_arg(text):
    if any(ord(c) > 126 for c in text):
        raise FEError("adb can only type ASCII; use a Maestro flow (inputText) for non-ASCII text")
    out = text.replace("%", "\\%").replace(" ", "%s")
    for ch in "&|;<>()$`\\\"'*?!#~":
        if ch != "\\":
            out = out.replace(ch, "\\" + ch)
    return out


def wait_for(dev, text=None, label=None, rid=None, timeout=15):
    deadline = time.time() + timeout
    while True:
        try:
            return find_node(screen_nodes(dev), text, label, rid)
        except FEError:
            if time.time() > deadline:
                raise
            time.sleep(1)


def _xy(s):
    x, y = s.split(",")
    return int(x), int(y)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("screen", "tap", "type", "swipe", "back", "wait"):
        p = sub.add_parser(name)
        p.add_argument("--device", required=True)
        p.add_argument("--platform", choices=["android", "ios"], default="android")
        p.add_argument("--max-chars", type=int, default=3000)
        if name in ("tap", "wait"):
            p.add_argument("--text"); p.add_argument("--label"); p.add_argument("--id")
        if name == "tap":
            p.add_argument("--point", type=_xy); p.add_argument("--index", type=int, default=0)
        if name == "wait":
            p.add_argument("--timeout", type=float, default=15)
        if name == "type":
            p.add_argument("--text", required=True)
        if name == "swipe":
            p.add_argument("--from", dest="src", type=_xy, required=True)
            p.add_argument("--to", type=_xy, required=True)
            p.add_argument("--ms", type=int, default=300)
    a = ap.parse_args()
    dev = Device(a.platform, a.device, "")
    if a.cmd == "tap":
        node = tap(dev, a.text, a.label, a.id, a.point, a.index)
        print("tapped %s %r" % (node.get("id"), node.get("text") or node.get("label", "")))
    elif a.cmd == "type":
        if dev.platform == "ios":
            devices.ios_type(dev, a.text)
        else:
            fe_common.run([devices.adb_path(), "-s", dev.id, "shell", "input", "text", adb_text_arg(a.text)])
        time.sleep(0.5)
    elif a.cmd == "swipe":
        devices.swipe(dev, a.src[0], a.src[1], a.to[0], a.to[1], a.ms)
        time.sleep(SETTLE_S)
    elif a.cmd == "back":
        devices.back(dev)
        time.sleep(SETTLE_S)
    elif a.cmd == "wait":
        node = wait_for(dev, a.text, a.label, a.id, a.timeout)
        print("found %s %r" % (node["id"], node["text"] or node["label"]))
    nodes = [n for n in screen_nodes(dev) if not n["rid"].startswith("com.android.systemui")]
    print(compress(nodes, a.max_chars))
    return 0


if __name__ == "__main__":
    cli(main)
