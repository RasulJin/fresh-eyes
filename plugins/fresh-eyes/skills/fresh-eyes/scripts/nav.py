#!/usr/bin/env python3
"""Navigation by the view hierarchy, printed as compact text (~1.5k chars a screen instead of ~15k of MCP JSON).

Android: uiautomator + adb (the Maestro CLI's hierarchy when the UI never gets idle). Only one process may hold
UiAutomation, and the Maestro MCP driver holds it, so the MCP is never used on Android. iOS: the HTTP API of the
Maestro XCTest driver that the MCP started (one MCP
inspect_screen on the simulator starts it). Coordinates are screenshot pixels on both platforms. Matching follows
Maestro: text/label are full-string, case-insensitive regexes; ids match by suffix.

CLI (all print the compressed screen after acting; --platform defaults to android):
  nav.py screen --device ID [--platform android|ios]
  nav.py tap    --device ID (--text RE | --label RE | --id SUFFIX | --point X,Y) [--index N]   (refused: an empty
                 one, a target under the open keyboard; a node half under it is tapped on its visible part)
  nav.py type   --device ID (--text "text" | [--env VAR] [--keychain SERVICE])   (Android: ASCII only; secrets:
                 the variable when set, else the macOS keychain item; never printed. Refused unless a text field
                 has focus in a fresh dump, and, for a password (VAR or SERVICE says "password"), one with the
                 password flag; the field then prints as ‹typed›)
  nav.py swipe  --device ID --from X,Y --to X,Y [--ms 300]   (refused from within 48 px of an edge: the system's)
  nav.py back   --device ID                      (iOS: edge swipe)
  nav.py wait   --device ID (--text RE | --label RE | --id SUFFIX) [--timeout 15]
  nav.py idle   --run RUN [--platform android|ios]   (seconds since the platform's last heartbeat)
  nav.py idle   --run RUN [--platform P] --until S (--cells ID,... | --requests FILE...)   (blocks up to S seconds:
                 "done" when those cells are recorded / requests answered, "stuck" when no heartbeat came, else
                 "running")
--run RUN on any of them refreshes <run>/<platform>/heartbeat, as every capture.py call does.
Every text nav.py, taps.py and capture.py print or store has the credentials of config `accounts` (env, else
keychain) cut out as ‹secret›.
"""
import argparse
import json
import os
import re
import sys
import time

import devices
import fe_common
from devices import Device
from fe_common import FEError, cli
from hierarchy import XCUI_TEXT_INPUTS, compress, normalize

SETTLE_S = 1.0


def _dump_uiautomator(dev):
    proc = fe_common.run([devices.adb_path(), "-s", dev.id, "exec-out", "uiautomator", "dump", "/dev/tty"],
                         timeout=DUMP_TIMEOUT_S)
    return (proc.stdout + proc.stderr).decode("utf-8", "replace")  # stderr: "could not get idle state" comes there


def _dump_maestro(dev):
    """`maestro hierarchy`: Maestro's driver reads the tree without waiting for an idle UI. Slow (~20 s: the CLI
    installs its driver and removes it on exit). Never on a device a Maestro MCP session drives: the CLI kills it."""
    from preflight import _maestro_path
    exe = _maestro_path()
    if not exe:
        raise FEError("no Maestro CLI found")
    devices._require_owned(dev, "install the Maestro driver")
    if devices.maestro_driver_running(dev):
        raise FEError("a Maestro driver already runs on %s (a Maestro MCP session?): the Maestro CLI would kill "
                      "it, so it was not run" % dev.id)
    try:
        return fe_common.run([exe, "--device", dev.id, "hierarchy"], timeout=180).stdout.decode("utf-8", "replace")
    finally:
        if devices.maestro_driver_running(dev):  # ours, left running: it would hold UiAutomation from uiautomator
            devices.stop_maestro_driver(dev)


IDLE = "idle state"  # uiautomator dump: "could not get idle state" on a screen that animates forever
DUMP_TIMEOUT_S = 20  # 0.5.13 welcome: dumps hung 40 s three times over (2 min) before the Maestro CLI was tried


def hierarchy(dev):
    """(nodes, source), credentials cut out of every text (fe_common.redact): whatever prints or stores a dump
    (nav.py, taps.py, capture.py) never shows a secret typed into a field without the password flag."""
    nodes, source = _hierarchy(dev)
    return fe_common.scrub(nodes), source


def _hierarchy(dev):
    """Android: uiautomator; when it fails (a UI that never gets idle), the Maestro CLI."""
    if dev.platform == "ios":
        base = devices.ios_driver(dev)
        raw = devices.ios_call(dev, "viewHierarchy", {"appIds": [], "excludeKeyboardElements": False}, base=base)
        return normalize(raw, "xctest", devices.ios_scale(dev, base)), "xctest"
    last = None
    for _ in range(3):
        try:
            return normalize(_dump_uiautomator(dev), "uiautomator"), "uiautomator"
        except FEError as e:
            last = e
            if IDLE in str(e) or "timed out" in str(e):
                break  # each retry waits as long again for the same answer
            time.sleep(0.7)
    try:
        return normalize(_dump_maestro(dev), "maestro"), "maestro"
    except FEError as e:
        raise FEError("no hierarchy: uiautomator: %s; maestro: %s" % (last, e))


def screen_nodes(dev):
    return hierarchy(dev)[0]


def _full(pattern, value):
    return bool(value) and re.fullmatch(pattern, value, flags=re.I | re.S) is not None


def find_node(nodes, text=None, label=None, rid=None, index=0):
    if not any((text, label, rid)) or "" in (text, label, rid):  # an empty id matches every node (0.5.12)
        raise FEError("name the element with a non-empty --text, --label or --id")
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


def _on_keyboard(node):
    return node["cls"] in ("Key", "Keyboard") or node["rid"].startswith(devices.GBOARD)


def visible_box(node, kb_top=None, screen_wh=None):
    """The part of the node a finger can reach: inside the screen and above an open keyboard (a tap below its
    top lands on a key: 0.5.12 opened Gboard's clipboard). FEError when nothing of it is left."""
    x, y, w, h = node["bounds"]
    right, bottom = x + w, y + h
    if screen_wh:
        x, y, right, bottom = max(x, 0), max(y, 0), min(right, screen_wh[0]), min(bottom, screen_wh[1])
        if right <= x or bottom <= y:
            raise FEError("%r is off screen at %s: scroll it into view first" % (_name(node), node["bounds"]))
    if kb_top is not None and not _on_keyboard(node):
        bottom = min(bottom, kb_top)
        if bottom <= y:
            raise FEError("%r is under the keyboard (its top y=%d): hide the keyboard first (nav.py back)"
                          % (_name(node), kb_top))
    return [x, y, right - x, bottom - y]


def _name(node):
    return node.get("text") or node.get("label") or node.get("rid") or node.get("id")


def tap(dev, text=None, label=None, rid=None, point=None, index=0):
    if point:
        node = {"id": None, "cls": "", "text": "", "label": "", "rid": "", "bounds": [point[0], point[1], 1, 1]}
        visible_box(node, devices.keyboard_top(dev, screen_nodes(dev) if dev.platform == "ios" else ()))
        devices.tap(dev, point[0], point[1])
        time.sleep(SETTLE_S)
        return dict(node, bounds=[point[0], point[1], 0, 0])
    nodes = screen_nodes(dev)
    node = find_node(nodes, text, label, rid, index)
    devices.tap(dev, *center({"bounds": visible_box(node, devices.keyboard_top(dev, nodes))}))
    time.sleep(SETTLE_S)
    return node


def adb_text_arg(text):
    """Escape for `adb shell input text` (adb joins args into one device shell command line)."""
    if any(ord(c) > 126 or ord(c) < 32 for c in text):
        raise FEError("adb can only type printable ASCII; use a Maestro flow (inputText) for other text")
    out = text.replace("\\", "\\\\")
    for ch in "&|;<>()$`\"'*?!#~":
        out = out.replace(ch, "\\" + ch)
    return out.replace("%", "\\%").replace(" ", "%s")


CHUNK = 20  # chars per `input text`: Android drops characters from a long one and while a field reformats
CHUNK_PAUSE_S = 0.15


def adb_text_chunks(text, size=CHUNK):
    """(raw, escaped) pieces of `size` characters, each escaped on its own so no escape is cut in half."""
    return [(text[i:i + size], adb_text_arg(text[i:i + size])) for i in range(0, len(text), size)]


def wait_for(dev, text=None, label=None, rid=None, timeout=15):
    deadline = time.time() + timeout
    while True:
        try:
            return find_node(screen_nodes(dev), text, label, rid)
        except FEError:
            if time.time() > deadline:
                raise
            time.sleep(1)


TYPED = "‹typed›"


def is_input(node):
    """A text field: Android EditText and its kin (android.widget.TextView is plain text), iOS input types."""
    return node["cls"].endswith(("EditText", "AutoCompleteTextView")) or node["cls"] in XCUI_TEXT_INPUTS


def secret_field(nodes, password):
    """The focused field a secret may go into: an input, with the password flag for a password. A refusal never
    prints the field's text (it may hold the email typed a moment ago)."""
    field = next((n for n in nodes if n["focused"]), None)
    if field is None or not is_input(field):
        raise FEError("no text field has focus (focused: %s): tap the field from a fresh `nav.py screen` first"
                      % (field["cls"] if field else "nothing"))
    if password and not field.get("password"):
        raise FEError("the focused field (%s) has no password flag: a password typed there shows in plain text. "
                      "Tap the password field from a fresh `nav.py screen` and type again"
                      % (field["rid"] or field["cls"]))
    return field


def secret(var=None, service=None):
    """A test credential: the environment variable when set, else the macOS keychain item (`security`)."""
    if var and os.environ.get(var):
        return os.environ[var]
    if service:
        proc = fe_common.run(["security", "find-generic-password", "-s", service, "-w"], check=False)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.decode("utf-8").rstrip("\n")
    raise FEError("not set: %s" % " and ".join(filter(None, (var and "environment variable " + var,
                                                              service and "keychain item " + service))))


POLL_S = 5


def _left(run, cells, requests):
    """What the navigator still has to do: its todo cells and its requests entries without done/failed."""
    left = 0
    if cells:
        status = {c["id"]: c["status"] for c in fe_common.read_json(os.path.join(run, "coverage.json"))["cells"]}
        unknown = [c for c in cells if c not in status]
        if unknown:
            raise FEError("not in coverage.json: %s" % ", ".join(unknown))
        left += sum(status[c] == "todo" for c in cells)
    for path in requests:
        left += sum(1 for e in fe_common.read_json(path)
                    if e.get("do") != "redo-cell" and "done" not in e and "failed" not in e)
    return left


def wait_idle(run, platform, until, cells=(), requests=()):
    """One blocking wait instead of polling: returns "done" as soon as nothing is left, else after `until` s
    "running" (a heartbeat came: wait again) or "stuck" (none came during the whole wait)."""
    beat = os.path.join(run, platform, "heartbeat")
    start = time.time()
    while True:
        try:
            left = _left(run, cells, requests)
        except ValueError:  # a requests.json mid-write: read it on the next poll
            left = None
        now = time.time()
        mtime = os.path.getmtime(beat) if os.path.exists(beat) else None
        if left == 0:
            state = "done"
        elif now - start >= until:
            state = "running" if mtime is not None and mtime >= start else "stuck"
        else:
            time.sleep(min(POLL_S, until - (now - start)))
            continue
        return {"state": state, "left": left, "idle_s": round(now - mtime) if mtime is not None else None,
                "did": fe_common.read_json(beat)["did"] if mtime is not None else None}


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
        p.add_argument("--run", help="the run folder: refreshes <run>/<platform>/heartbeat")
        if name in ("tap", "wait"):
            p.add_argument("--text"); p.add_argument("--label"); p.add_argument("--id")
        if name == "tap":
            p.add_argument("--point", type=_xy); p.add_argument("--index", type=int, default=0)
        if name == "wait":
            p.add_argument("--timeout", type=float, default=15)
        if name == "type":  # a secret is read here (environment, else keychain), never printed or passed as text
            p.add_argument("--text"); p.add_argument("--env", metavar="VAR")
            p.add_argument("--keychain", metavar="SERVICE")
        if name == "swipe":
            p.add_argument("--from", dest="src", type=_xy, required=True)
            p.add_argument("--to", type=_xy, required=True)
            p.add_argument("--ms", type=int, default=300)
    p = sub.add_parser("idle", help="seconds since <run>/<platform>/heartbeat was refreshed (null: never)")
    p.add_argument("--run", required=True); p.add_argument("--platform", choices=["android", "ios"], default="android")
    p.add_argument("--until", type=float, help="block up to this many seconds (under Bash's 10-minute limit)")
    p.add_argument("--cells", help="comma list: the navigator's cell ids")
    p.add_argument("--requests", nargs="+", default=[], help="requests-mode navigator: its requests.json files")
    a = ap.parse_args()
    if a.cmd == "type" and (a.text is None) == (a.env is None and a.keychain is None):
        ap.error("type needs --text, or --env and/or --keychain")
    if a.cmd == "idle" and a.until:
        if not (a.cells or a.requests):
            ap.error("idle --until needs --cells or --requests")
        cells = [c for c in (a.cells or "").split(",") if c]
        print(json.dumps(wait_idle(a.run, a.platform, a.until, cells, a.requests)))
        return 0
    if a.cmd == "idle":
        beat = os.path.join(a.run, a.platform, "heartbeat")
        age = round(time.time() - os.path.getmtime(beat)) if os.path.exists(beat) else None
        print(json.dumps({"idle_s": age, "did": fe_common.read_json(beat)["did"] if age is not None else None}))
        return 0
    if a.run:
        fe_common.heartbeat(a.run, a.platform, "nav.py " + a.cmd)
        fe_common.load_secrets(a.run)
    dev = Device(a.platform, a.device, "")
    typed_secret = a.cmd == "type" and a.text is None
    if a.cmd == "tap":
        node = tap(dev, a.text, a.label, a.id, a.point, a.index)
        print("tapped %s %r" % (node.get("id"), node.get("text") or node.get("label", "")))
    elif a.cmd == "type":
        text = a.text
        if typed_secret:  # a fresh dump: the field that has focus now, not the one an older dump showed
            text = secret(a.env, a.keychain)
            fe_common.add_secret(text)
            secret_field(screen_nodes(dev), "password" in ("%s %s" % (a.env, a.keychain)).lower())
        if dev.platform == "ios":
            devices.ios_type(dev, text)
        else:
            devices._require_owned(dev, "send input")
            for k, (raw, arg) in enumerate(adb_text_chunks(text)):  # all escaped (and checked) before typing
                time.sleep(CHUNK_PAUSE_S if k else 0)
                fe_common.run([devices.adb_path(), "-s", dev.id, "shell", "input", "text", arg],
                              redact=(text, raw, arg) if a.text is None else ())
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
    nodes = [dict(n, text=TYPED) if typed_secret and n["focused"] else n
             for n in screen_nodes(dev) if not n["rid"].startswith("com.android.systemui")]
    print(compress(nodes, a.max_chars))
    return 0


if __name__ == "__main__":
    cli(main)
