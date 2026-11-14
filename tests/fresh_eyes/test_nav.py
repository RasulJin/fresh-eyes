import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from tests.fresh_eyes.helpers import FIXTURES, SCRIPTS, tmpdir
import nav
from devices import Device
from fe_common import FEError, heartbeat, write_json
from hierarchy import normalize


def settings_nodes():
    with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as f:
        return normalize(f.read(), "uiautomator")


class FindTest(unittest.TestCase):
    def test_find_by_text_full_match_case_insensitive(self):
        n = nav.find_node(settings_nodes(), text="network & internet")
        self.assertEqual(n["text"], "Network & internet")

    def test_find_by_regex(self):
        self.assertEqual(nav.find_node(settings_nodes(), text="Network.*")["text"], "Network & internet")

    def test_partial_text_does_not_match(self):
        with self.assertRaises(FEError):
            nav.find_node(settings_nodes(), text="Network")

    def test_find_by_id_suffix(self):
        n = nav.find_node(settings_nodes(), rid="search_action_bar")
        self.assertTrue(n["rid"].endswith("search_action_bar"))

    def test_index_picks_nth(self):
        nodes = settings_nodes()
        first = nav.find_node(nodes, rid="android:id/title")
        second = nav.find_node(nodes, rid="android:id/title", index=1)
        self.assertNotEqual(first["id"], second["id"])

    def test_not_found_lists_visible_texts(self):
        with self.assertRaises(FEError) as e:
            nav.find_node(settings_nodes(), text="Nope")
        self.assertIn("Apps", str(e.exception))

    def test_center(self):
        self.assertEqual(nav.center({"bounds": [10, 20, 100, 50]}), (60, 45))


class InputTest(unittest.TestCase):
    def test_long_text_goes_in_escaped_chunks_that_rejoin(self):
        text = "https://example.com/p?a=1&b=50%off " * 3 + "back\\slash end"
        chunks = nav.adb_text_chunks(text, size=7)
        self.assertEqual("".join(raw for raw, _ in chunks), text)
        self.assertTrue(all(len(raw) <= 7 and arg == nav.adb_text_arg(raw) for raw, arg in chunks))
        self.assertEqual("".join(arg for _, arg in chunks), nav.adb_text_arg(text))  # no escape cut in half

    def test_ascii_text_escaped_for_adb(self):
        self.assertEqual(nav.adb_text_arg("hello world & co"), "hello%sworld%s\\&%sco")

    def test_control_chars_refused_backslash_escaped(self):
        with self.assertRaises(FEError):
            nav.adb_text_arg("line one\nreboot")
        self.assertEqual(nav.adb_text_arg("a\\b"), "a\\\\b")

    def test_secret_redacted_from_errors(self):
        import fe_common
        with self.assertRaises(FEError) as e:
            fe_common.run(["sh", "-c", "exit 3", "hunter2-secret"], redact=("hunter2-secret",))
        self.assertNotIn("hunter2", str(e.exception))

    def test_non_ascii_refused(self):
        with self.assertRaises(FEError):
            nav.adb_text_arg("Привет")

    def test_tap_uses_center_of_found_node(self):
        calls = []
        with mock.patch.object(nav, "screen_nodes", lambda dev: settings_nodes()), \
                mock.patch.object(nav.devices, "tap", lambda dev, x, y: calls.append((x, y))), \
                mock.patch.object(nav.time, "sleep", lambda s: None):
            node = nav.tap(Device("android", "emulator-5560", "fresh-eyes-pixel"), text="Apps")
        self.assertEqual(node["text"], "Apps")
        self.assertEqual(calls, [nav.center(node)])


class HeartbeatTest(unittest.TestCase):
    def idle(self, run):
        out = subprocess.run([sys.executable, os.path.join(SCRIPTS, "nav.py"), "idle", "--run", run],
                             stdout=subprocess.PIPE, check=True).stdout
        return json.loads(out)

    def test_any_capture_or_nav_call_under_the_run_refreshes_its_platforms_heartbeat(self):
        with tmpdir() as run:
            self.assertEqual(self.idle(run), {"idle_s": None, "did": None})
            heartbeat(os.path.join(run, "android", "feed", "default-none", "scroll-1"), "android", "snap")
            self.assertFalse(os.path.exists(os.path.join(run, "android", "heartbeat")))  # not a run: no coverage.json
            write_json(os.path.join(run, "coverage.json"), {"cells": []})
            heartbeat(os.path.join(run, "android", "feed", "default-none", "scroll-1"), "android", "capture.py snap")
            beat = os.path.join(run, "android", "heartbeat")
            os.utime(beat, (os.path.getmtime(beat) - 700,) * 2)
            out = self.idle(run)
            self.assertGreaterEqual(out["idle_s"], 700)
            self.assertEqual(out["did"], "capture.py snap")
            heartbeat(run, "android", "nav.py wait")  # nav.py --run <run>
            self.assertLess(self.idle(run)["idle_s"], 60)


if __name__ == "__main__":
    unittest.main()
