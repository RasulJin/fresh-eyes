import os
import unittest
from unittest import mock

from tests.fresh_eyes.helpers import FIXTURES
import nav
from devices import Device
from fe_common import FEError
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


if __name__ == "__main__":
    unittest.main()
