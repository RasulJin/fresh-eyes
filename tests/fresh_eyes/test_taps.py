import contextlib
import io
import os
import sys
import unittest
from unittest import mock

from tests.fresh_eyes.helpers import make_png, tmpdir
import devices
import taps
from fe_common import FEError, write_json
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


class SwipeTapTest(unittest.TestCase):
    def test_the_delay_is_the_runs_motion_ceiling(self):
        cell = dict(CELL, id="android/feed/default/drag-interrupt", probe="drag-interrupt")
        calls = []
        with tmpdir() as d:
            cov = run_dir(d, [cell], False)
            write_json(cov, {"run_id": "r", "cells": [cell], "motion_ceiling_ms": 250})
            argv = ["taps.py", "swipe-tap", "--platform", "android", "--device", "e", "--from", "500,1500",
                    "--to", "500,2300", "--x", "540", "--y", "800", "--coverage", cov, "--cell", cell["id"]]
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(devices, "swipe_then_tap", lambda dev, *a, **kw: calls.append(kw)):
                taps.main()
                with mock.patch.object(sys, "argv", argv + ["--delay-ms", "120"]):
                    taps.main()
        self.assertEqual([c["delay_ms"] for c in calls], [250, 120])


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
