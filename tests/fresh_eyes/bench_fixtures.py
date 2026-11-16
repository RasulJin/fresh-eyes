"""Regenerate the seeded bench (references/bench/): six screens of a made-up travel app, each with one human-class
defect (or, for the control, a tap that landed on a neighbour), snapped through the real capture.snap so the packs
carry the same files and candidates a device run would. A dev tool, not a test: needs Pillow and ffmpeg.

    python3 tests/fresh_eyes/bench_fixtures.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from unittest import mock
from xml.sax.saxutils import quoteattr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tests.fresh_eyes.helpers import SCRIPTS  # noqa: E402  (puts the scripts on sys.path)
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import capture  # noqa: E402
import devices  # noqa: E402
import motion  # noqa: E402
import nav  # noqa: E402
from fe_common import read_json, write_json  # noqa: E402

OUT = os.path.join(os.path.dirname(SCRIPTS), "references", "bench")
DP = 1.5
W, H = 540, 1200
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
SYMBOLS = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"  # ♡ ✎ ✈ ➤
BG, CARD, INK, GREY, LINE = (247, 245, 241), (255, 255, 255), (28, 28, 30), (110, 110, 118), (226, 224, 220)
ACCENT, WHITE = (37, 99, 235), (255, 255, 255)
CLS = {"View": "android.view.ViewGroup", "Text": "android.widget.TextView", "Edit": "android.widget.EditText",
       "Scroll": "android.widget.ScrollView", "Image": "android.widget.ImageView"}


def el(cls, b, text="", size=14, **kw):
    """A node in dp: b = [x, y, w, h]; size = font dp; kw: desc, rid, click, focused, fill, radius, color, bold,
    align (left|center|right), stroke, kids."""
    return dict(kw, cls=cls, b=b, text=text, size=size)


def px(v):
    return int(round(v * DP))


def draw(img, nodes):
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, px(24)], fill=BG)
    d.text((px(16), px(5)), "9:41", font=ImageFont.truetype(BOLD, px(11)), fill=INK)
    for n in nodes:
        x, y, w, h = [px(v) for v in n["b"]]
        if n.get("fill") or n.get("stroke"):
            d.rounded_rectangle([x, y, x + w - 1, y + h - 1], radius=px(n.get("radius", 0)), fill=n.get("fill"),
                                outline=n.get("stroke"), width=px(1) if n.get("stroke") else 0)
        if n["text"] or n.get("hint"):
            font = ImageFont.truetype(SYMBOLS if n.get("symbol") else BOLD if n.get("bold") else FONT, px(n["size"]))
            label = n["text"] or n.get("hint")
            tw = d.textlength(label, font=font)
            pad = px(n.get("pad", 0))
            align = n.get("align", "left")
            tx = x + pad if align == "left" or tw > w - 2 * pad else (
                x + (w - tw) / 2 if align == "center" else x + w - pad - tw)
            layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))  # clipped to the node, as a one-line TextView is
            ImageDraw.Draw(layer).text((tx - x, (h - px(n["size"]) * 1.15) / 2), label, font=font,
                                       fill=n.get("color", GREY if n.get("hint") else INK))
            img.paste(layer, (x, y), layer)
        draw(img, n.get("kids", []))


def xml(nodes, pkg="com.example.wayfarer"):
    def one(n):
        x, y, w, h = [px(v) for v in n["b"]]
        rid = n.get("rid", "")
        attrs = {"class": CLS[n["cls"]], "text": n["text"],
                 "content-desc": n.get("desc", ""), "resource-id": rid, "package": pkg,
                 "clickable": str(bool(n.get("click"))).lower(), "focused": str(bool(n.get("focused"))).lower(),
                 "enabled": "true", "password": "false", "bounds": "[%d,%d][%d,%d]" % (x, y, x + w, y + h)}
        kids = "".join(one(k) for k in n.get("kids", []))
        return "<node %s>%s</node>" % (" ".join("%s=%s" % (k, quoteattr(v)) for k, v in attrs.items()), kids)
    root = {"cls": "View", "b": [0, 0, W / DP, H / DP], "text": "", "kids": nodes}
    return '<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0">%s</hierarchy>' % one(root)


def snap(pack, nodes, kb_top=None, extra=(), pkg="com.example.wayfarer", under=None):
    """Draw the screen, then capture.snap with the device swapped for this drawing."""
    img = Image.new("RGB", (W, H), BG)
    if under:
        under(img)
    draw(img, nodes)
    for paint in extra:
        paint(img)
    tmp = tempfile.mkdtemp()
    shot = os.path.join(tmp, "screen.png")
    img.save(shot)
    fakes = {"screenshot": lambda dev, dst: shutil.copyfile(shot, dst) and dst, "px_per_dp": lambda d: DP,
             "keyboard_top": lambda d, n=(): kb_top}
    with mock.patch.multiple(devices, **fakes), mock.patch.object(nav, "_dump_uiautomator", lambda d: xml(nodes, pkg)), \
            mock.patch.object(nav, "_dump_maestro", lambda d: (_ for _ in ()).throw(RuntimeError("no maestro"))):
        code = capture.snap("android", "seeded", pack, keyboard=kb_top is not None)
    shutil.rmtree(tmp)
    assert code == 0, (pack, code)
    meta = read_json(os.path.join(pack, "meta.json"))
    meta.update(time="2026-11-16T12:00:00", device="seeded-bench", app_running=True)
    write_json(os.path.join(pack, "meta.json"), meta)
    return img


def header(title, sub=None):
    out = [el("Text", [16, 36, 328, 32], title, 24, bold=True, rid="screen-title")]
    if sub:
        out.append(el("Text", [16, 70, 328, 20], sub, 13, color=GREY))
    return out


def booking():
    """Overlap by a pinned element: the last row sits under the pay bar and the list cannot scroll further."""
    rows = [("Flight BER → LIS", "Mon 12 May, 10:40", 186), ("Flight LIS → BER", "Thu 15 May, 18:05", 172),
            ("Hotel Alfama, 3 nights", "12–15 May", 264), ("Lisbon city pass", "72 h, 2 people", 42),
            ("Tram card", "3 days, 2 people", 13), ("Tile museum", "2 tickets", 36),
            ("Fado dinner", "Tue 13 May, 20:30", 90), ("Day trip to Sintra", "Wed 14 May", 78),
            ("Airport transfer", "Thu 15 May, 15:30", 38)]
    cards = []
    for i, (title, sub, price) in enumerate(rows):
        y = 104 + i * 72
        cards.append(el("View", [16, y, 328, 64], fill=CARD, radius=12, rid="booking-row", kids=[
            el("Text", [28, y + 10, 220, 22], title, 15, bold=True),
            el("Text", [28, y + 34, 220, 18], sub, 12, color=GREY),
            el("Text", [260, y + 20, 72, 24], "€%d" % price, 15, bold=True, align="right")]))
    total = sum(r[2] for r in rows)
    nodes = header("Booking summary", "Lisbon · 12–15 May · 2 travellers") + [
        el("Scroll", [0, 96, 360, 704], rid="booking-list", kids=cards),
        el("View", [0, 702, 360, 98], fill=CARD, rid="pay-bar", kids=[
            el("Text", [16, 714, 120, 18], "Total", 12, color=GREY),
            el("Text", [16, 734, 120, 28], "€%d" % total, 20, bold=True),
            el("View", [150, 714, 194, 52], fill=ACCENT, radius=26, click=True, desc="Continue to payment",
               rid="pay-button", kids=[el("Text", [150, 714, 194, 52], "Continue to payment", 15, bold=True,
                                          color=WHITE, align="center")])])]
    pack = os.path.join(OUT, "android", "booking", "default-none")
    snap(pack, nodes)
    meta = read_json(os.path.join(pack, "meta.json"))
    meta.update(scroll_pages=0, scroll_stop="end", scroll_restored=True)  # the list is at its end: nothing moved
    write_json(os.path.join(pack, "meta.json"), meta)


def flight():
    """Arithmetic: 10:40 + 2 h 05 min lands 12:45 in the same time zone; the screen says 13:45."""
    nodes = header("Berlin → Rome", "Mon 12 May · Direct · Economy") + [
        el("View", [16, 104, 328, 150], fill=CARD, radius=14, rid="flight-card", kids=[
            el("Text", [32, 122, 110, 36], "10:40", 28, bold=True, rid="depart-time"),
            el("Text", [32, 162, 140, 18], "BER · Berlin", 12, color=GREY),
            el("Text", [130, 132, 100, 18], "2 h 05 min", 12, color=GREY, align="center", rid="duration"),
            el("View", [140, 154, 80, 2], fill=LINE),
            el("Text", [218, 122, 110, 36], "13:45", 28, bold=True, align="right", rid="arrive-time"),
            el("Text", [188, 162, 140, 18], "FCO · Rome", 12, color=GREY, align="right"),
            el("Text", [32, 214, 296, 18], "Times are local", 12, color=GREY)]),
        el("View", [16, 270, 328, 104], fill=CARD, radius=14, rid="price-card", kids=[
            el("Text", [32, 286, 200, 20], "2 adults × €129", 14),
            el("Text", [32, 326, 120, 28], "Total", 16, bold=True),
            el("Text", [228, 326, 100, 28], "€258", 20, bold=True, align="right", rid="price-total")]),
        el("View", [16, 720, 328, 52], fill=ACCENT, radius=26, click=True, rid="select-flight", kids=[
            el("Text", [16, 720, 328, 52], "Select flight", 16, bold=True, color=WHITE, align="center")])]
    snap(os.path.join(OUT, "android", "flight", "default-none"), nodes)


def profile_edit():
    """Largest font: the middle button of three cuts its label mid-word."""
    def field(y, label, value):
        return [el("Text", [16, y, 328, 26], label, 19, color=GREY),
                el("Edit", [16, y + 30, 328, 56], value, 21, fill=CARD, radius=10, pad=14, click=True)]
    nodes = [el("Text", [16, 34, 328, 40], "Edit profile", 32, bold=True, rid="screen-title")]
    nodes += field(96, "Name", "Mara Lind") + field(194, "Home city", "Lisbon") + field(292, "About", "Slow trains")
    for x, w, label, rid in ((16, 100, "Cancel", "cancel"), (124, 112, "Save changes", "save"),
                             (244, 100, "Preview", "preview")):
        nodes.append(el("View", [x, 708, w, 60], radius=30, click=True, rid=rid, desc=label,
                        fill=ACCENT if rid == "save" else None, stroke=None if rid == "save" else GREY, kids=[
                            el("Text", [x, 708, w, 60], label, 21, bold=True, pad=12, align="center",
                               color=WHITE if rid == "save" else INK)]))
    snap(os.path.join(OUT, "android", "profile-edit", "font-xl-none"), nodes)


def traveller(following):
    btn = "Following" if following else "Follow"
    return header("Mara Lind", "Lisbon · 14 trips") + [
        el("View", [16, 104, 64, 64], fill=(214, 196, 178), radius=32, rid="avatar"),
        el("Text", [96, 110, 120, 22], "%d" % (129 if following else 128), 17, bold=True, rid="followers-count"),
        el("Text", [96, 134, 120, 18], "followers", 12, color=GREY),
        el("View", [232, 112, 112, 44], radius=22, click=True, rid="follow-button", desc="Follow",
           fill=CARD if following else ACCENT, stroke=GREY if following else None, kids=[
               el("Text", [232, 112, 112, 44], btn, 15, bold=True, align="center",
                  color=INK if following else WHITE)]),
        el("Text", [16, 186, 328, 20], "Trips", 15, bold=True),
        el("View", [16, 214, 160, 120], fill=(190, 208, 222), radius=12, click=True, desc="Porto in spring"),
        el("View", [184, 214, 160, 120], fill=(222, 206, 184), radius=12, click=True, desc="Seville by train")]


def profile():
    """State after an action: «Follow» became «Following», the screen-reader name stayed «Follow»."""
    pack = os.path.join(OUT, "android", "traveller", "default-tap-everything")
    snap(pack, traveller(False))
    snap(os.path.join(pack, "tap-1"), traveller(True))
    result = "new: 129, Following; gone: 128, Follow"
    write_json(os.path.join(pack, "taps.json"), [{"n": 1, "label": "Follow", "by": "label", "pack": "tap-1",
                                                  "changed": True, "result": result}])
    tap_json(os.path.join(pack, "tap-1"), [288, 134], [232, 112, 112, 44], "follow-button", "Follow", result)


def tap_json(after, point, bounds, rid, label, result, changed=True, by="label"):
    """tap.json as taps.py tap writes it: the point, the node it hit and that node's bounds at the tap."""
    write_json(os.path.join(after, "tap.json"), {
        "time": "2026-11-16T12:00:00", "point": [px(v) for v in point], "by": by,
        "node": {"cls": CLS["View"], "rid": rid, "text": "", "label": label}, "bounds": [px(v) for v in bounds],
        "keyboard_top": None, "screen_wh": [W, H], "source": "uiautomator", "snap_exit": 0, "settled": True,
        "pixels_changed": changed, "changed": changed, "result": result})


def comments(field_y, kb=False):
    nodes = header("Comments", "Porto in spring") + [
        el("View", [16, 104 + i * 70, 328, 62], fill=CARD, radius=12, kids=[
            el("Text", [28, 112 + i * 70, 300, 20], who, 13, bold=True),
            el("Text", [28, 134 + i * 70, 300, 20], what, 13)])
        for i, (who, what) in enumerate((("Ines", "The tram at 8 is empty, take it!"),
                                         ("Tom", "Saved this for October."),
                                         ("Mara", "Ribeira at sunset, not at noon.")))]
    nodes += [el("Edit", [12, field_y, 280, 48], "", 15, fill=CARD, stroke=GREY, radius=24, pad=16, click=True,
                 focused=kb, hint="Add a comment…", rid="comment-input"),
              el("View", [300, field_y, 48, 48], fill=ACCENT, radius=24, click=True, desc="Send", rid="send", kids=[
                  el("Text", [300, field_y, 48, 48], "➤", 18, align="center", color=WHITE, symbol=True)])]
    return nodes


def keyboard_paint(top_dp):
    def paint(img):
        d = ImageDraw.Draw(img)
        top = px(top_dp)
        d.rectangle([0, top, W, H], fill=(209, 212, 219))
        for r in range(4):
            for c in range(10):
                x, y = px(4 + c * 35.2), top + px(12 + r * 52)
                d.rounded_rectangle([x, y, x + px(31), y + px(42)], radius=px(5), fill=WHITE)
    return paint


def keyboard_case():
    """Keyboard transition: the comment field rides up, stops 200 ms halfway, then jumps again."""
    rest, kb = 488, 540  # field top with the keyboard up, keyboard top (dp)
    pack = os.path.join(OUT, "android", "comments", "keyboard-keyboard-open")
    snap(pack, comments(rest, kb=True), kb_top=px(kb), extra=[keyboard_paint(kb)])
    frames = tempfile.mkdtemp()
    fps, low = 30, 740
    for i in range(45):  # 1.5 s: still, first move, a 200 ms stop halfway, the second move, still
        t = i / float(fps)
        k = min(1.0, max(0.0, (t - 0.2) / 0.3))  # the keyboard rises 0.2-0.5 s
        if t < 0.2:
            y = low
        elif t < 0.4:
            y = low + (614 - low) * (t - 0.2) / 0.2
        elif t < 0.6:
            y = 614
        elif t < 0.75:
            y = 614 + (rest - 614) * (t - 0.6) / 0.15
        else:
            y = rest
        img = Image.new("RGB", (W, H), BG)
        draw(img, comments(y))
        keyboard_paint(800 - (800 - kb) * k)(img)
        draw(img, comments(y)[-2:])  # the field is drawn over the rising keyboard: it moves, it is never covered
        img.save(os.path.join(frames, "f%03d.png" % i))
    os.makedirs(os.path.join(pack, "kb-show"), exist_ok=True)
    subprocess.check_call(["ffmpeg", "-v", "error", "-y", "-framerate", str(fps), "-i", os.path.join(frames, "f%03d.png"),
                           "-vf", "scale=270:600", "-pix_fmt", "yuv420p", os.path.join(pack, "kb-show", "video.mp4")])
    shutil.rmtree(frames)
    rep = motion.keyboard(pack)
    assert rep["flags"] == ["kb-show: double_jump"], rep["flags"]
    shutil.rmtree(os.path.join(pack, "kb-show", "kb_frames"), ignore_errors=True)


def post(scrolled):
    dy = -50 if scrolled else 0  # the comment field took focus and the list stayed 50 dp up
    return header("Lisbon long weekend") + [
        el("Image", [0, 104 + dy, 360, 300], fill=(160, 186, 204), rid="post-photo"),
        el("View", [16, 416 + dy, 40, 40], click=True, desc="Like", rid="like", kids=[
            el("Text", [16, 416 + dy, 40, 40], "♡", 22, align="center", symbol=True)]),
        el("View", [64, 416 + dy, 40, 40], click=True, desc="Comment", rid="comment", kids=[
            el("Text", [64, 416 + dy, 40, 40], "✎", 20, align="center", symbol=True)]),
        el("View", [296, 416 + dy, 48, 48], fill=(232, 238, 252), radius=24, click=True, desc="Plan this trip",
           rid="plan-trip", kids=[el("Text", [296, 416 + dy, 48, 48], "✈", 20, align="center", color=ACCENT,
                                     symbol=True)]),
        el("View", [16, 472 + dy, 328, 36], fill=CARD, radius=18, stroke=LINE, click=True, rid="place-chip",
           kids=[el("Text", [16, 472 + dy, 328, 36], "Lisbon, Portugal", 14, pad=14)]),
        el("Text", [16, 520 + dy, 328, 20], "Three days of trams, tiles and fado.", 14),
        el("Edit", [12, 740, 336, 48], "", 15, fill=CARD, stroke=GREY, radius=24, pad=16, click=True,
           hint="Add a comment…", rid="comment-input")]


def map_app():
    def paint(img):
        d = ImageDraw.Draw(img)
        d.rectangle([0, px(24), W, H], fill=(233, 236, 229))
        for k in range(0, W, px(40)):
            d.line([k, px(24), k + px(120), H], fill=WHITE, width=px(6))
        d.ellipse([px(168), px(380), px(192), px(404)], fill=(220, 60, 50))
    return [el("View", [12, 32, 336, 48], fill=CARD, radius=24, rid="com.example.maps:id/search_box", kids=[
        el("Text", [12, 32, 336, 48], "Lisbon, Portugal", 15, pad=18, rid="com.example.maps:id/search_text")])], paint


def tapped_neighbour():
    """Control: the tap meant for «Plan this trip» landed on the place chip (the list had moved), which opens the
    map app as designed. tap.json shows it; «Plan this trip» itself was never tapped."""
    pack = os.path.join(OUT, "android", "post", "default-tap-everything")
    snap(pack, post(False))
    nodes, paint = map_app()
    snap(os.path.join(pack, "tap-2"), nodes, under=paint, pkg="com.example.maps")
    snap(os.path.join(pack, "tap-1"), post(False))  # like: the heart turns
    # an old-style tap at the som's coordinates: the list had moved 50 dp, the point was on the place chip by then
    rows = [("Like", "like", "tap-1", [36, 436], [16, 416, 40, 40], False, "no change"),
            ("Plan this trip", "place-chip", "tap-2", [320, 440], [16, 422, 328, 36], True,
             "new: Lisbon, Portugal; gone: Like, Comment, Plan this trip, Three days of trams, tiles and fado.")]
    write_json(os.path.join(pack, "taps.json"), [{"n": k, "label": label, "by": "som", "pack": sub, "changed": ch,
                                                  "result": res} for k, (label, _, sub, _, _, ch, res) in
                                                 enumerate(rows, 1)])
    for label, rid, sub, point, bounds, ch, res in rows:
        tap_json(os.path.join(pack, sub), point, bounds, rid, "Like" if rid == "like" else "", res, ch, "som")


SPEC = """# Spec — Wayfarer (seeded bench)
Updated: 2026-11-16

## Global
- G-copy: English only, short labels. (source: bench)

## booking — Booking summary
Purpose: every booked item with its price, then payment.
- must booking#1: the pay bar stays pinned at the bottom with the total. (source: bench)
### Invariants
- inv booking#2: the total equals the sum of the row prices. (source: bench)

## flight — Flight details
Purpose: one flight's times and price before booking.
- must flight#1: departure and arrival in local time with the airport. (source: bench)
### Invariants
- inv flight#2: arrival = departure + duration, in the arrival airport's time zone; Berlin and Rome are both on
  Central European Time. (source: bench)
- inv flight#3: the total equals adults × the fare. (source: bench)

## profile-edit — Edit profile
Purpose: change name, home city and about.
- must profile-edit#1: Cancel, Save changes and Preview stay in one row. (source: bench)

## traveller — Traveller profile
Purpose: another traveller's trips; follow them.
- must traveller#1: Follow turns into Following at once and the count goes up by one. (source: bench)

## comments — Comments
Purpose: read and add comments under a trip.
- must comments#1: tapping the field opens the keyboard with the field above it. (source: bench)

## post — Post
Purpose: one trip post.
- must post#1: the place chip opens the phone's map app at that place. (source: bench)
- must post#2: «Plan this trip» opens the trip planner with this post's place. (source: bench)
"""

CASES = [
    ("SEED-overlap", "booking", "bug", "Booking summary: the last row «Airport transfer €38» lies under the pinned "
     "pay bar and the list cannot scroll further, so that row can never be read."),
    ("SEED-arithmetic", "flight", "bug", "Flight details: 10:40 from Berlin plus 2 h 05 min shows arrival 13:45 in "
     "Rome, same time zone: it should be 12:45, the arrival is an hour off."),
    ("SEED-cut-font-xl", "profile-edit", "bug", "Edit profile at the largest font: the middle button «Save changes» "
     "is cut mid-word at its edges, the label does not fit the button."),
    ("SEED-state-after-tap", "traveller", "bug", "Traveller profile: after tapping «Follow» the button reads "
     "«Following», but its screen-reader name (content-desc) still says «Follow»."),
    ("SEED-keyboard", "comments", "bug", "Comments: when the keyboard opens, the comment field moves up, stops "
     "halfway for about 200 ms, then jumps again: two moves instead of one."),
    ("SEED-tap-shift", "post", "control", "«Plan this trip» opens the trip planner. In this pack the tap landed on "
     "the «Lisbon, Portugal» place chip next to it (tap.json: the point lies in the chip), which opens the map app "
     "as designed; a finding that «Plan this trip» opens a map app is a false positive."),
]


def main():
    shutil.rmtree(os.path.join(OUT, "android"), ignore_errors=True)
    for make in (booking, flight, profile_edit, profile, keyboard_case, tapped_neighbour):
        make()
    for root, _, files in os.walk(OUT):  # a palette of 128 colours: a third of the size, the same to the eye
        for name in files:
            if name.endswith(".png"):
                path = os.path.join(root, name)
                Image.open(path).convert("RGB").quantize(128).save(path, optimize=True)
    with open(os.path.join(OUT, "spec.md"), "w", encoding="utf-8") as f:
        f.write(SPEC)
    write_json(os.path.join(OUT, "cases.json"), [
        {"id": cid, "group": "seeded", "kind": kind, "screen": screen, "platform": "android",
         "expect": {"description": text, "bbox": None}} for cid, screen, kind, text in CASES])
    print(OUT)


if __name__ == "__main__":
    main()
