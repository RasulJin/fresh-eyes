import json
import os
import unittest

from tests.fresh_eyes.helpers import FIXTURES
from hierarchy import XCUI_TYPES, HierarchyError, compress, match_key, normalize

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


# Shape copied from a real Maestro iOS driver /viewHierarchy reply (Settings app), trimmed. Frames are in points.
XCTEST = {"depth": 9, "axElement": {"children": [
    {"elementType": 2, "label": "Настройки", "frame": {"X": 0, "Y": 0, "Width": 402, "Height": 874}, "children": [
        {"elementType": 1, "frame": {"X": 16, "Y": 600, "Width": 370, "Height": 52}},
        {"elementType": 1, "frame": {"X": 16, "Y": 600, "Width": 370, "Height": 52}, "children": [
            {"elementType": 9, "label": "Основные", "frame": {"X": 16, "Y": 600, "Width": 370, "Height": 52}}]},
        {"elementType": 25, "frame": {"X": 0, "Y": 0, "Width": 402, "Height": 54}, "children": [
            {"elementType": 1, "label": "Заряд аккумулятора 100 %",
             "frame": {"X": 339.33, "Y": 26, "Width": 27.33, "Height": 13}}]},
        {"elementType": 9, "label": "Камера", "identifier": "com.apple.settings.camera", "enabled": True,
         "frame": {"X": 16, "Y": 433.3333, "Width": 370, "Height": 52}, "children": [
             {"elementType": 48, "label": "Камера", "frame": {"X": 29.67, "Y": 445, "Width": 103, "Height": 29}}]},
        {"elementType": 9, "label": "Камера", "identifier": "com.apple.settings.camera", "enabled": True,
         "frame": {"X": 16, "Y": 433.3333, "Width": 370, "Height": 52}},
        {"elementType": 49, "label": "Поиск", "value": "Ист", "placeholderValue": "Город", "hasFocus": True,
         "frame": {"X": 16, "Y": 500, "Width": 370, "Height": 36}},
        {"elementType": 40, "label": "Тёмная тема", "value": 1,
         "frame": {"X": 300, "Y": 560, "Width": 51, "Height": 31}},
        {"elementType": 1, "label": "Вертикальная полоса прокрутки, 2 страницы", "value": "0\u00a0%",
         "frame": {"X": 369, "Y": 116, "Width": 30, "Height": 672},
         "children": [{"elementType": 1, "frame": {"X": 396, "Y": 119, "Width": 3, "Height": 406}}]},
        {"elementType": 19, "frame": {"X": 0, "Y": 538, "Width": 402, "Height": 336}, "children": [
            {"elementType": 20, "label": "й", "frame": {"X": 3, "Y": 550, "Width": 33, "Height": 42}}]}]}]}}


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



class XctestTest(unittest.TestCase):
    def setUp(self):
        self.nodes = normalize(json.dumps(XCTEST), "xctest", 3.0)
        self.by_cls = {}
        for n in self.nodes:
            self.by_cls.setdefault(n["cls"], []).append(n)

    def test_type_table_matches_xcui_raw_values(self):
        self.assertEqual(len(XCUI_TYPES), 83)
        self.assertEqual((XCUI_TYPES[9], XCUI_TYPES[19], XCUI_TYPES[48], XCUI_TYPES[75]),
                         ("Button", "Keyboard", "StaticText", "Cell"))

    def test_points_scaled_to_pixels(self):
        cam = next(n for n in self.by_cls["Button"] if n["label"] == "Камера")
        self.assertEqual(cam["bounds"], [48, 1300, 1110, 156])

    def test_status_bar_and_keys_dropped_keyboard_kept(self):
        self.assertNotIn("StatusBar", self.by_cls)
        self.assertNotIn("Key", self.by_cls)
        self.assertEqual(self.by_cls["Keyboard"][0]["bounds"][1], 1614)

    def test_fields(self):
        b = next(n for n in self.by_cls["Button"] if n["label"] == "Камера")
        self.assertEqual((b["text"], b["label"], b["rid"], b["clickable"]),
                         ("", "Камера", "com.apple.settings.camera", True))
        self.assertEqual(self.by_cls["StaticText"][0]["text"], "Камера")
        self.assertFalse(self.by_cls["StaticText"][0]["clickable"])
        self.assertEqual(self.by_cls["StaticText"][0]["parent"], b["id"])
        f = self.by_cls["TextField"][0]
        self.assertEqual((f["text"], f["label"], f["focused"]), ("Ист", "Поиск", True))
        self.assertEqual(self.by_cls["Switch"][0]["text"], "")  # a control's label is not drawn text

    def test_scroll_indicator_dropped(self):
        self.assertFalse(any("прокрутки" in n["label"] for n in self.nodes))

    def test_duplicate_siblings_dropped(self):
        self.assertEqual(sorted(b["label"] for b in self.by_cls["Button"]), ["Камера", "Основные"])


if __name__ == "__main__":
    unittest.main()
