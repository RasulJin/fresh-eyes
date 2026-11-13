import contextlib
import io
import os
import sys
import unittest
from unittest import mock

from tests.fresh_eyes.helpers import tmpdir
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


if __name__ == "__main__":
    unittest.main()
