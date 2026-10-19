import os
import unittest
from unittest import mock

from tests.fresh_eyes.helpers import tmpdir
import devices
from fe_common import FEError, write_json
from taps import guard

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


if __name__ == "__main__":
    unittest.main()
