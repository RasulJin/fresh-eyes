import json
import os
import unittest

from tests.fresh_eyes.helpers import FIXTURES
from hierarchy import HierarchyError, compress, match_key, normalize

FIX_XML = os.path.join(FIXTURES, "settings_uiautomator.xml")
FIX_CLI = os.path.join(FIXTURES, "settings_maestro_cli.json")

# Shape copied from a real MCP inspect_screen reply (Settings app).
MCP_COMPACT = {
    "ui_schema": {"platform": "android",
                  "abbreviations": {"b": "bounds", "txt": "text", "rid": "resource-id", "a11y": "content-desc",
                                    "cls": "class", "c": "children"},
                  "defaults": {"enabled": True, "clickable": False, "focused": False}},
    "elements": [{"enabled": False, "c": [
        {"b": "[0,0][1080,2400]", "cls": "android.widget.FrameLayout", "c": [
            {"b": "[42,84][1038,273]", "rid": "com.android.settings:id/search_action_bar",
             "cls": "android.widget.LinearLayout", "clickable": True, "c": [
                 {"b": "[189,143][578,214]", "txt": "Search Settings", "cls": "android.widget.TextView"}]},
            {"b": "[221,352][354,409]", "txt": "Импорт мест", "a11y": "import", "cls": "android.widget.TextView"},
        ]}]}],
}


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class HierarchyTest(unittest.TestCase):
    def test_uiautomator_bounds_to_xywh(self):
        xml = ('<hierarchy><node bounds="[0,63][1080,210]" text="Импорт мест" class="android.widget.TextView" '
               'resource-id="" content-desc="" clickable="false" focused="false" enabled="true"/></hierarchy>')
        n = normalize(xml, "uiautomator")
        self.assertEqual(n[0]["bounds"], [0, 63, 1080, 147])
        self.assertEqual(n[0]["text"], "Импорт мест")
        self.assertIsNone(n[0]["parent"])

    def test_uiautomator_fixture_with_trailing_banner(self):
        nodes = normalize(_read(FIX_XML), "uiautomator")
        self.assertGreater(len(nodes), 10)
        self.assertTrue(any(n["text"] == "Network & internet" for n in nodes))
        child = next(n for n in nodes if n["text"] == "Network & internet")
        self.assertIsNotNone(child["parent"])
        self.assertGreater(child["depth"], 0)

    def test_maestro_compact(self):
        nodes = normalize(json.dumps(MCP_COMPACT), "maestro")
        search = next(n for n in nodes if n["rid"].endswith("search_action_bar"))
        self.assertTrue(search["clickable"])
        self.assertEqual(search["bounds"], [42, 84, 996, 189])
        label = next(n for n in nodes if n["text"] == "Импорт мест")
        self.assertEqual(label["label"], "import")
        self.assertTrue(label["enabled"])

    def test_maestro_cli_fixture(self):
        nodes = normalize(_read(FIX_CLI), "maestro-cli")
        self.assertGreater(len(nodes), 10)
        row = next(n for n in nodes if n["text"] == "Apps")
        self.assertEqual(len(row["bounds"]), 4)
        self.assertFalse(row["clickable"])

    def test_empty_raises(self):
        for raw, src in [("", "uiautomator"), ("<hierarchy/>", "uiautomator"), ("{}", "maestro"),
                         ("not xml", "uiautomator"), ('{"attributes":{},"children":[]}', "maestro-cli")]:
            with self.assertRaises(HierarchyError, msg=(raw, src)):
                normalize(raw, src)

    def test_unknown_source_raises(self):
        with self.assertRaises(HierarchyError):
            normalize("<hierarchy/>", "xcui")

    def test_match_key(self):
        nodes = normalize(json.dumps(MCP_COMPACT), "maestro")
        label = next(n for n in nodes if n["text"] == "Импорт мест")
        self.assertEqual(match_key(label), ("", "Импорт мест", "import", "android.widget.TextView"))

    def test_compress_lists_meaningful_nodes(self):
        out = compress(normalize(json.dumps(MCP_COMPACT), "maestro"))
        self.assertIn('"Search Settings"', out)
        self.assertIn("⚑", out)  # clickable marker
        self.assertNotIn("FrameLayout", out)  # empty containers are skipped

    def test_compress_respects_limit(self):
        nodes = normalize(_read(FIX_XML), "uiautomator")
        out = compress(nodes, max_chars=300)
        self.assertLessEqual(len(out), 340)
        self.assertTrue(out.rstrip().splitlines()[-1].startswith("…"))


if __name__ == "__main__":
    unittest.main()
