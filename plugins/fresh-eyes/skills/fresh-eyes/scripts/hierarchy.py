#!/usr/bin/env python3
"""Normalize a view hierarchy (uiautomator XML, Maestro MCP compact JSON, Maestro CLI JSON) into flat nodes.

Node: {id, parent, depth, cls, text, label, rid, bounds:[x,y,w,h], clickable, focused, enabled}
CLI:
  hierarchy.py normalize --source uiautomator|maestro|maestro-cli --in FILE --out nodes.json
  hierarchy.py compress --in nodes.json [--max-chars N]
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET

from fe_common import FEError, cli, read_json, write_json

_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


class HierarchyError(FEError):
    pass


def _bounds(s):
    m = _BOUNDS.search(s or "")
    if not m:
        return None
    x1, y1, x2, y2 = (int(v) for v in m.groups())
    return [x1, y1, x2 - x1, y2 - y1]


def _truthy(v, default=False):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).lower() == "true"


class _Builder:
    def __init__(self):
        self.nodes = []

    def add(self, parent, depth, bounds, cls="", text="", label="", rid="", clickable=False, focused=False,
            enabled=True):
        node = {"id": "n%d" % len(self.nodes), "parent": parent, "depth": depth, "cls": cls or "",
                "text": text or "", "label": label or "", "rid": rid or "", "bounds": bounds,
                "clickable": clickable, "focused": focused, "enabled": enabled}
        self.nodes.append(node)
        return node["id"]


def _from_uiautomator(raw):
    end = raw.rfind("</hierarchy>")
    if end < 0:
        raise HierarchyError("uiautomator dump has no </hierarchy>")
    try:
        root = ET.fromstring(raw[:end + len("</hierarchy>")])
    except ET.ParseError as e:
        raise HierarchyError("uiautomator dump is not valid XML: %s" % e)
    b = _Builder()

    def walk(el, parent, depth):
        for child in el.findall("node"):
            bounds = _bounds(child.get("bounds"))
            pid = parent
            if bounds is not None:
                pid = b.add(parent, depth, bounds, child.get("class"), child.get("text"), child.get("content-desc"),
                            child.get("resource-id"), _truthy(child.get("clickable")),
                            _truthy(child.get("focused")), _truthy(child.get("enabled"), True))
            walk(child, pid, depth + 1)

    walk(root, None, 0)
    return b.nodes


def _from_maestro_compact(raw):
    data = json.loads(raw)
    if not isinstance(data, dict) or "elements" not in data:
        raise HierarchyError("maestro JSON has no 'elements'")
    defaults = (data.get("ui_schema") or {}).get("defaults") or {}
    b = _Builder()

    def flag(el, key, fallback):
        return _truthy(el.get(key), _truthy(defaults.get(key), fallback))

    def walk(items, parent, depth):
        for el in items or []:
            bounds = _bounds(el.get("b"))
            pid = parent
            if bounds is not None:
                pid = b.add(parent, depth, bounds, el.get("cls"), el.get("txt"), el.get("a11y"), el.get("rid"),
                            flag(el, "clickable", False), flag(el, "focused", False), flag(el, "enabled", True))
            walk(el.get("c"), pid, depth + 1)

    walk(data["elements"], None, 0)
    return b.nodes


def _from_maestro_cli(raw):
    data = json.loads(raw)
    if not isinstance(data, dict) or "children" not in data:
        raise HierarchyError("maestro CLI JSON has no 'children'")
    b = _Builder()

    def walk(el, parent, depth):
        a = el.get("attributes") or {}
        bounds = _bounds(a.get("bounds"))
        pid = parent
        if bounds is not None:
            pid = b.add(parent, depth, bounds, a.get("class"), a.get("text"), a.get("accessibilityText"),
                        a.get("resource-id"), _truthy(a.get("clickable")), _truthy(a.get("focused")),
                        _truthy(a.get("enabled"), True))
        for child in el.get("children") or []:
            walk(child, pid, depth + 1)

    walk(data, None, 0)
    return b.nodes


_PARSERS = {"uiautomator": _from_uiautomator, "maestro": _from_maestro_compact, "maestro-cli": _from_maestro_cli}


def normalize(raw, source):
    if source not in _PARSERS:
        raise HierarchyError("unknown hierarchy source: %s" % source)
    if not raw or not raw.strip():
        raise HierarchyError("empty hierarchy")
    try:
        nodes = _PARSERS[source](raw)
    except ValueError as e:  # json.JSONDecodeError
        raise HierarchyError("hierarchy is not valid JSON: %s" % e)
    if not nodes:
        raise HierarchyError("hierarchy has no nodes with bounds")
    return nodes


def match_key(node):
    return (node["rid"], node["text"], node["label"], node["cls"])


def compress(nodes, max_chars=6000):
    lines = []
    for n in nodes:
        if not (n["text"] or n["label"] or n["rid"] or n["clickable"]):
            continue
        x, y, w, h = n["bounds"]
        line = "%s [%d,%d,%d,%d] %s" % (n["id"], x, y, w, h, n["cls"].rsplit(".", 1)[-1])
        if n["text"]:
            line += ' "%s"' % n["text"]
        if n["label"] and n["label"] != n["text"]:
            line += " (%s)" % n["label"]
        if n["rid"]:
            line += " #" + n["rid"].split(":id/")[-1]
        if n["clickable"]:
            line += " ⚑"
        if n["focused"]:
            line += " ●"
        lines.append(line)
    out, size = [], 0
    for i, line in enumerate(lines):
        if size + len(line) + 1 > max_chars - 20:
            out.append("… %d more" % (len(lines) - i))
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("normalize")
    n.add_argument("--source", required=True, choices=sorted(_PARSERS))
    n.add_argument("--in", dest="src", required=True)
    n.add_argument("--out", required=True)
    c = sub.add_parser("compress")
    c.add_argument("--in", dest="src", required=True)
    c.add_argument("--max-chars", type=int, default=6000)
    args = ap.parse_args()
    if args.cmd == "normalize":
        with open(args.src, encoding="utf-8") as f:
            nodes = normalize(f.read(), args.source)
        write_json(args.out, nodes)
        print(json.dumps({"nodes": len(nodes), "out": args.out}))
    else:
        print(compress(read_json(args.src), args.max_chars))
    return 0


if __name__ == "__main__":
    cli(main)
