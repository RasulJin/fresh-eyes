import contextlib
import io
import os
import sys
import unittest
from unittest import mock

from tests.fresh_eyes.helpers import make_png, tmpdir
import devices
import taps
from fe_common import FEError, read_json, write_json
from taps import edge_point, guard

CELL = {"id": "android/feed/default/rapid-tap", "platform": "android", "screen": "feed", "state": "default",
        "probe": "rapid-tap", "mutating": False, "status": "todo", "evidence": [], "note": ""}


def run_dir(d, cells, mutating_allowed):
    write_json(os.path.join(d, "coverage.json"), {"run_id": "r", "mode": "quick", "cells": cells, "unchecked": []})
    write_json(os.path.join(d, "preflight.json"), {"mutating_allowed": mutating_allowed})
    return os.path.join(d, "coverage.json")


class TapsGuardTest(unittest.TestCase):
    def test_taps_refuses_unknown_cell(self):
        with tmpdir() as d:
            with self.assertRaises(FEError):
                guard(run_dir(d, [CELL], True), "android/feed/default/nope")

    def test_taps_refuses_mutating_when_disallowed(self):
        with tmpdir() as d:
            cell = dict(CELL, id="android/feed/default/double-submit", probe="double-submit", mutating=True)
            with self.assertRaises(FEError):
                guard(run_dir(d, [cell], False), cell["id"])

    def test_taps_allows_plain_cell(self):
        with tmpdir() as d:
            self.assertEqual(guard(run_dir(d, [CELL], False), CELL["id"])["id"], CELL["id"])

    def test_missing_preflight_counts_as_disallowed(self):
        with tmpdir() as d:
            cov = run_dir(d, [dict(CELL, mutating=True)], True)
            os.remove(os.path.join(d, "preflight.json"))
            with self.assertRaises(FEError):
                guard(cov, CELL["id"])


class EdgeTest(unittest.TestCase):
    def test_edge_point_four_sides(self):
        box = [100, 200, 40, 20]  # 2 dp at 2.5 px/dp = 5 px outside each edge, centred along it
        self.assertEqual([edge_point(box, s, 2, 2.5) for s in ("top", "bottom", "left", "right")],
                         [(120, 195), (120, 224), (95, 210), (144, 210)])


class GestureTest(unittest.TestCase):
    """drag-interrupt steps snap before and after themselves (0.5.12: sub-snaps retaken after all gestures)."""
    def run_gesture(self, cmd, extra, snap_codes=(0, 0), coverage_extra=None):
        cell = dict(CELL, id="android/feed/default/drag-interrupt", probe="drag-interrupt", marker=["Лента"])
        events = []
        codes = list(snap_codes)

        def snap(platform, dev, out, **kw):
            events.append(("snap", os.path.basename(out), kw.get("marker", ())))
            return codes.pop(0)

        def around(platform, dev, out, action, settle=2.5, ceiling_ms=None, call=None):
            events.append(("record", os.path.basename(out)))
            call()
        with tmpdir() as d:
            cov = run_dir(d, [cell], False)
            write_json(cov, dict({"run_id": "r", "cells": [cell]}, **(coverage_extra or {})))
            pack = os.path.join(d, "android", "feed", "default-drag-interrupt")
            argv = ["taps.py", cmd, "--platform", "android", "--device", "e", "--pack", pack,
                    "--coverage", cov, "--cell", cell["id"]] + extra
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(taps.capture, "snap", snap), mock.patch.object(taps.capture, "around", around), \
                    mock.patch.object(taps.time, "sleep", lambda s: None), \
                    mock.patch.object(devices, "swipe", lambda dev, *a: events.append(("swipe",) + a)), \
                    mock.patch.object(devices, "drag_then_back", lambda dev, *a, **kw: events.append(("drag-back",))), \
                    mock.patch.object(devices, "swipe_then_tap", lambda dev, *a, **kw: events.append(kw)):
                taps.main()
        return events

    def test_before_on_the_screen_then_the_recorded_gesture_then_where_it_left(self):
        events = self.run_gesture("drag-back", ["--from", "100,1200", "--to", "900,1200"])
        self.assertEqual(events, [("snap", "before", ("Лента",)), ("record", "drag-back"), ("drag-back",),
                                  ("snap", "drag-back", ())])

    def test_slow_drags_forty_then_sixty_percent(self):
        events = self.run_gesture("slow", ["--from", "100,1200", "--to", "1100,1200"])
        self.assertEqual([e for e in events if e[0] == "swipe"],
                         [("swipe", 100, 1200, 500, 1200, 2000), ("swipe", 100, 1200, 700, 1200, 2000)])

    def test_no_gesture_off_the_screen(self):
        with self.assertRaises(FEError):
            self.run_gesture("drag-back", ["--from", "1,2", "--to", "3,4"], snap_codes=(5,))

    def test_swipe_tap_delay_is_the_runs_motion_ceiling(self):
        args = ["--from", "500,1500", "--to", "500,2300", "--x", "540", "--y", "800"]
        events = self.run_gesture("swipe-tap", args, coverage_extra={"motion_ceiling_ms": 250}) + \
            self.run_gesture("swipe-tap", args + ["--delay-ms", "120"], coverage_extra={"motion_ceiling_ms": 250})
        self.assertEqual([e["delay_ms"] for e in events if isinstance(e, dict)], [250, 120])


class Clock(object):
    """time.time / time.sleep of taps.py: a screenshot takes 0.1 s, a sleep what it asks."""
    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


class SettleTest(unittest.TestCase):
    def shots(self, d, names):
        frames = {"A": [], "B": [(0, 40, 30, 60, (0, 0, 0), 0)], "half": [(0, 40, 15, 60, (0, 0, 0), 0)],
                  "C": [(0, 0, 30, 30, (200, 0, 0), 0)]}
        paths = {k: make_png(os.path.join(d, k + ".png"), 30, 120, rects=v) for k, v in frames.items()}
        seq, clock = iter(names), Clock()

        def shot(dev, dst):
            clock.t += 0.1
            with open(paths[next(seq)], "rb") as src, open(dst, "wb") as out:
                out.write(src.read())
            return dst
        return paths["A"], clock, shot

    def run_settle(self, names, max_s=3.0):
        with tmpdir() as d:
            before, clock, shot = self.shots(d, names)
            with mock.patch.object(devices, "screenshot", shot), mock.patch.object(taps, "time", clock):
                return taps.settle(None, before, max_s=max_s), clock.t

    def test_the_snap_waits_for_the_screen_the_tap_led_to(self):
        # 0.5.11: "Попутчики" slid in ~1 s after the tap, the snap had the old screen: a false dead tap
        out, t = self.run_settle(["A", "A", "half", "B", "B"])
        self.assertEqual((out["settled"], out["changed"]), (True, True))
        self.assertAlmostEqual(t, 5 * 0.1 + 4 * 0.4)  # five frames 0.4 s apart: the last two match

    def test_a_dead_tap_is_only_called_so_after_the_whole_wait(self):
        out, t = self.run_settle(["A"] * 10)
        self.assertEqual((out["settled"], out["changed"]), (True, False))
        self.assertGreaterEqual(t, 3.0)

    def test_an_endless_animation_ends_at_the_limit_unsettled(self):
        out, t = self.run_settle(["B", "C"] * 10)
        self.assertEqual((out["settled"], out["changed"]), (False, True))
        self.assertGreaterEqual(t, 3.0)
        self.assertLess(t, 3.6)


def node(i, text, y, rid="", cls="android.widget.TextView"):
    return {"id": "n%d" % i, "parent": None, "depth": 1, "cls": cls, "text": text, "label": "", "rid": rid,
            "bounds": [0, y, 1080, 120], "clickable": True, "focused": False, "enabled": True}


ROWS = [node(1, "Row A", 400, "app:id/row_a"), node(2, "Row B", 700, "app:id/row_b"),
        node(3, "Row C", 1000, "app:id/row_c")]
SCROLLED = [dict(n, id="n%d" % (k + 7), bounds=[0, n["bounds"][1] - 300, 1080, 120]) for k, n in enumerate(ROWS)]


class TapByNodeTest(unittest.TestCase):
    """0.5.12's false high: a tap at a mark's old point after the list had scrolled under it."""
    def run_tap(self, args, now=SCROLLED, kb_top=None):
        taps_, snaps = [], []
        with tmpdir() as d:
            cov = run_dir(d, [dict(CELL, id="android/feed/default/tap-everything", probe="tap-everything")], False)
            pack = os.path.join(d, "android", "feed", "default-tap-everything")
            write_json(os.path.join(pack, "nodes.json"), ROWS)
            write_json(os.path.join(pack, "som.json"), [{"n": k + 1, "node_ids": [n["id"]], "bbox": n["bounds"]}
                                                        for k, n in enumerate(ROWS)])
            after = os.path.join(pack, "tap-1")
            argv = ["taps.py", "tap", "--platform", "android", "--device", "e", "--after", after,
                    "--coverage", cov, "--cell", "android/feed/default/tap-everything"] + args
            out = io.StringIO()
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out), \
                    mock.patch.object(taps.nav, "hierarchy", lambda dev: (now, "uiautomator")), \
                    mock.patch.object(devices, "keyboard_top", lambda dev, nodes=(): kb_top), \
                    mock.patch.object(devices, "screenshot", lambda dev, dst: make_png(dst, 1080, 2400)), \
                    mock.patch.object(devices, "tap", lambda dev, x, y: taps_.append((x, y))), \
                    mock.patch.object(taps, "settle", lambda dev, before: {"settled": True, "changed": True,
                                                                            "ms": 800}), \
                    mock.patch.object(taps.capture, "snap", lambda p, dv, o, **kw: snaps.append(o) or 0):
                taps.main()
            return taps_, snaps, read_json(os.path.join(after, "tap.json"))

    def test_a_mark_is_tapped_where_its_node_is_now(self):
        tapped, snaps, rec = self.run_tap(["--som", "3"])
        self.assertEqual(tapped, [(540, 760)])
        self.assertEqual((rec["point"], rec["bounds"], rec["by"]), ([540, 760], [0, 700, 1080, 120], "som"))
        self.assertTrue(snaps[0].endswith("tap-1"))

    def test_old_coordinates_are_refused(self):
        with self.assertRaises(FEError) as e:
            self.run_tap(["--x", "540", "--y", "1060", "--expect-id", "row_c"])
        self.assertIn("outside", str(e.exception))
        self.assertEqual(self.run_tap(["--x", "540", "--y", "760", "--expect-id", "row_c"])[0], [(540, 760)])

    def test_a_point_needs_the_node_it_is_meant_for(self):
        for args in (["--x", "540", "--y", "760"], ["--id", ""], [], ["--id", "row_a", "--som", "1"]):
            with self.assertRaises(SystemExit, msg=args), contextlib.redirect_stderr(io.StringIO()):
                self.run_tap(args)

    def test_a_node_gone_or_under_the_keyboard_is_refused(self):
        with self.assertRaises(FEError):
            self.run_tap(["--som", "3"], now=SCROLLED[:2])
        with self.assertRaises(FEError) as e:
            self.run_tap(["--id", "row_c"], kb_top=650)
        self.assertIn("under the keyboard", str(e.exception))


class TapsJsonTest(unittest.TestCase):
    """0.5.12 thread: folders tap-7/8/9 were not rows 7-9, rows written by hand, emoji and likes `changed: false`."""
    def test_each_tap_appends_its_row_and_snaps_into_the_rows_folder(self):
        afters = [ROWS + [node(9, "Скопировано", 2000)], ROWS]  # a toast; then nothing moved

        def snap(platform, dev, out, **kw):
            write_json(os.path.join(out, "nodes.json"), afters.pop(0))
            write_json(os.path.join(out, "meta.json"), {"keyboard_up": False})
            return 0
        with tmpdir() as d:
            cov = run_dir(d, [dict(CELL, id="android/feed/default/tap-everything", probe="tap-everything")], False)
            pack = os.path.join(d, "android", "feed", "default-tap-everything")
            write_json(os.path.join(pack, "nodes.json"), ROWS)
            write_json(os.path.join(pack, "som.json"), [{"n": 2, "node_ids": ["n2"], "bbox": ROWS[1]["bounds"]}])
            for args in (["--som", "2"], ["--text", "Row C|Row A", "--index", "1"]):
                argv = ["taps.py", "tap", "--platform", "android", "--device", "e", "--pack", pack,
                        "--coverage", cov, "--cell", "android/feed/default/tap-everything"] + args
                with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()), \
                        mock.patch.object(taps.nav, "hierarchy", lambda dev: (ROWS, "uiautomator")), \
                        mock.patch.object(devices, "keyboard_top", lambda dev, nodes=(): None), \
                        mock.patch.object(devices, "screenshot", lambda dev, dst: make_png(dst, 1080, 2400)), \
                        mock.patch.object(devices, "tap", lambda dev, x, y: None), \
                        mock.patch.object(taps, "settle", lambda dev, before: {"settled": True, "changed": False,
                                                                                "ms": 3000}), \
                        mock.patch.object(taps.capture, "snap", snap):
                    taps.main()
            rows = read_json(os.path.join(pack, "taps.json"))
            self.assertEqual([(r["n"], r["pack"], r["label"], r["changed"]) for r in rows],
                             [(1, "tap-1", "Row B", True), (2, "tap-2", "Row C", False)])
            self.assertEqual((rows[0]["som"], rows[0]["result"], rows[1]["result"]),
                             (2, "new: Скопировано", "no change"))
            self.assertEqual(read_json(os.path.join(pack, "tap-2", "tap.json"))["node"]["text"], "Row C")

    def test_what_changed_reads_focus_keyboard_and_moves(self):
        field = node(5, "", 1500, "app:id/input", "android.widget.EditText")
        focused = [dict(field, focused=True)]
        self.assertEqual(taps.what_changed([field], focused, False, True, False),
                         (True, "focus: app:id/input; keyboard up"))
        moved = [dict(field, bounds=[0, 900, 1080, 120])]
        self.assertEqual(taps.what_changed([field], moved, False, False, False), (True, "moved"))
        clock = node(6, "12:01", 0, "com.android.systemui:id/clock")
        self.assertEqual(taps.what_changed([field, clock], [field, dict(clock, text="12:02")], False, False, False),
                         (False, "no change"))
        self.assertEqual(taps.what_changed([field], None, False, None, True), (True, "no after snap, the screen changed"))


class RapidHoldTest(unittest.TestCase):
    def test_rapid_returns_six_seconds_after_the_first_tap(self):
        # 0.5.11: the share sheet came 2.6 s after three taps, the 2.2 s video missed it: a false "nothing opened"
        clock = Clock()
        with tmpdir() as d:
            cov = run_dir(d, [CELL], False)
            argv = ["taps.py", "rapid", "--platform", "android", "--device", "e", "--x", "1", "--y", "2",
                    "--coverage", cov, "--cell", CELL["id"]]
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(taps, "time", clock), \
                    mock.patch.object(devices, "rapid_taps", lambda *a: clock.sleep(0.3)):
                taps.main()
                self.assertAlmostEqual(clock.t, 6.0)
                with mock.patch.object(sys, "argv", argv + ["--hold-ms", "0"]):
                    taps.main()
                self.assertAlmostEqual(clock.t, 6.3)


if __name__ == "__main__":
    unittest.main()
