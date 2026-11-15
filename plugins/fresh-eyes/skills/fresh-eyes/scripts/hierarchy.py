"""Normalize a view hierarchy (uiautomator XML, Maestro iOS driver JSON, `maestro hierarchy` JSON) into flat
nodes, and compress them to text. Bounds are multiplied by `scale` (iOS sources are in points, screenshots in pixels).

Node: {id, parent, depth, cls, text, label, rid, bounds:[x,y,w,h], clickable, focused, enabled}
"""
import json
import re
import xml.etree.ElementTree as ET

from fe_common import FEError

_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_PERCENT = re.compile(r"^\d+\s?%$")


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
    def __init__(self, scale=1.0):
        self.nodes, self.scale = [], scale

    def add(self, parent, depth, bounds, cls="", text="", label="", rid="", clickable=False, focused=False,
            enabled=True):
        bounds = [int(round(v * self.scale)) for v in bounds]
        node = {"id": "n%d" % len(self.nodes), "parent": parent, "depth": depth, "cls": cls or "",
                "text": text or "", "label": label or "", "rid": rid or "", "bounds": bounds,
                "clickable": clickable, "focused": focused, "enabled": enabled}
        self.nodes.append(node)
        return node["id"]


def _from_uiautomator(raw, scale):
    end = raw.rfind("</hierarchy>")
    if end < 0:
        raise HierarchyError("uiautomator dump has no </hierarchy>: %s" % raw.strip()[:120])
    try:
        root = ET.fromstring(raw[:end + len("</hierarchy>")])
    except ET.ParseError as e:
        raise HierarchyError("uiautomator dump is not valid XML: %s" % e)
    b = _Builder(scale)

    def walk(el, parent, depth):
        for child in el.findall("node"):
            bounds = _bounds(child.get("bounds"))
            pid = parent
            if bounds is not None:
                text = "" if _truthy(child.get("password")) else child.get("text")  # never print a typed secret
                pid = b.add(parent, depth, bounds, child.get("class"), text, child.get("content-desc"),
                            child.get("resource-id"), _truthy(child.get("clickable")),
                            _truthy(child.get("focused")), _truthy(child.get("enabled"), True))
            walk(child, pid, depth + 1)

    walk(root, None, 0)
    return b.nodes


# XCUIElement.ElementType raw values 0..82, in order.
XCUI_TYPES = ("Any Other Application Group Window Sheet Drawer Alert Dialog Button RadioButton RadioGroup CheckBox "
              "DisclosureTriangle PopUpButton ComboBox MenuButton ToolbarButton Popover Keyboard Key NavigationBar "
              "TabBar TabGroup Toolbar StatusBar Table TableRow TableColumn Outline OutlineRow Browser CollectionView "
              "Slider PageIndicator ProgressIndicator ActivityIndicator SegmentedControl Picker PickerWheel Switch "
              "Toggle Link Image Icon SearchField ScrollView ScrollBar StaticText TextField SecureTextField DatePicker "
              "TextView Menu MenuItem MenuBar MenuBarItem Map WebView IncrementArrow DecrementArrow Timeline "
              "RatingIndicator ValueIndicator SplitGroup Splitter RelevanceIndicator ColorWell HelpTag Matte DockItem "
              "Ruler RulerMarker Grid LevelIndicator Cell LayoutArea LayoutItem Handle Stepper Tab TouchBar "
              "StatusItem").split()
# ponytail: iOS has no "clickable" flag; a RN Pressable without accessibilityRole is "Other" and is not seen here.
XCUI_TAPPABLE = {"Button", "Cell", "Link", "Switch", "Toggle", "TextField", "SecureTextField", "SearchField",
                 "TextView", "Tab", "SegmentedControl", "Slider", "PopUpButton", "ToolbarButton", "CheckBox",
                 "RadioButton", "Stepper", "PickerWheel", "MenuItem"}
XCUI_TEXT_INPUTS = {"TextField", "SecureTextField", "SearchField", "TextView"}


def _labeled(el):
    return bool(el.get("label")) or any(_labeled(c) for c in el.get("children") or [])


def _from_xctest(raw, scale):
    data = json.loads(raw)
    if not isinstance(data, dict) or "axElement" not in data:
        raise HierarchyError("iOS driver JSON has no 'axElement'")
    b = _Builder(scale)
    seen = set()

    def walk(el, parent, depth):
        t = el.get("elementType", 0)
        cls = XCUI_TYPES[t] if 0 <= t < len(XCUI_TYPES) else "Type%d" % t
        if cls == "StatusBar" or (cls == "Other" and _PERCENT.match(str(el.get("value") or ""))
                                  and not any(_labeled(c) for c in el.get("children") or [])):
            return  # OS chrome, like com.android.systemui; scroll indicators ("37 %" with an unlabeled thumb)
        f, pid = el.get("frame"), parent
        if f:
            bounds = [f["X"], f["Y"], f["Width"], f["Height"]]
            key = (parent, t, el.get("label"), tuple(bounds))
            if el.get("label") and key in seen:  # the AX tree lists some labeled elements twice;
                return                             # unlabeled same-frame siblings are distinct wrappers
            seen.add(key)
            # text = what is drawn (as uiautomator's text); a container's label is only its accessibility label
            value = el.get("value") if isinstance(el.get("value"), str) else ""
            text = el.get("label") if cls == "StaticText" else \
                (value or el.get("placeholderValue")) if cls in XCUI_TEXT_INPUTS else ""
            pid = b.add(parent, depth, bounds, cls, text, el.get("label"), el.get("identifier"),
                        cls in XCUI_TAPPABLE, bool(el.get("hasFocus")), el.get("enabled", True))
        if cls == "Keyboard":  # keep its frame (keyboard top), drop the keys: Android dumps no IME either
            return
        for child in el.get("children") or []:
            walk(child, pid, depth + 1)

    walk(data["axElement"], None, 0)
    return b.nodes


def _from_maestro(raw, scale):
    """`maestro hierarchy` on Android: Maestro's TreeNode JSON (the CLI may print lines before it). Its driver reads
    the tree without waiting for an idle UI. No password flag: Android gives accessibility a password's dots only."""
    start = raw.find("{")
    if start < 0:
        raise HierarchyError("maestro hierarchy printed no JSON: %s" % raw.strip()[:120])
    data = json.JSONDecoder().raw_decode(raw, start)[0]
    b = _Builder(scale)

    def walk(el, parent, depth):
        for child in el.get("children") or []:
            a, pid = child.get("attributes") or {}, parent
            bounds = _bounds(a.get("bounds"))
            if bounds is not None:
                pid = b.add(parent, depth, bounds, a.get("class"), a.get("text"), a.get("accessibilityText"),
                            a.get("resource-id"), _truthy(child.get("clickable", a.get("clickable"))),
                            _truthy(child.get("focused", a.get("focused"))),
                            _truthy(child.get("enabled", a.get("enabled")), True))
            walk(child, pid, depth + 1)

    walk(data, None, 0)
    return b.nodes


_PARSERS = {"uiautomator": _from_uiautomator, "xctest": _from_xctest, "maestro": _from_maestro}


def normalize(raw, source, scale=1.0):
    if source not in _PARSERS:
        raise HierarchyError("unknown hierarchy source: %s" % source)
    if not raw or not raw.strip():
        raise HierarchyError("empty hierarchy")
    try:
        nodes = _PARSERS[source](raw, scale)
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

