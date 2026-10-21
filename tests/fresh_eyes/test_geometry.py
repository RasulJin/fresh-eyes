import unittest

import tests.fresh_eyes.helpers  # noqa: F401  (sys.path)
from geometry import (overlaps, partially_visible, repeated_strings, run_all, small_targets, still_clipped,
                      truncation_candidates, under_keyboard)

SCREEN = [1080, 2400]


def node(nid, bounds, parent="root", text="", label="", clickable=False, focused=False,
         cls="android.widget.TextView", rid=""):
    return {"id": nid, "parent": parent, "depth": 1, "cls": cls, "text": text, "label": label, "rid": rid,
            "bounds": bounds, "clickable": clickable, "focused": focused, "enabled": True}


ROOT = node("root", [0, 0, 1080, 2400], parent=None, cls="android.widget.FrameLayout")


class PartialVisibilityTest(unittest.TestCase):
    def test_bottom_row_partially_visible(self):
        row = node("row", [0, 2300, 1080, 400], cls="android.view.ViewGroup")
        c = partially_visible([ROOT, row], SCREEN)
        self.assertEqual([x["node_ids"] for x in c], [["row"]])
        self.assertEqual(c[0]["check"], "partially_visible")

    def test_fully_visible_row_not_flagged(self):
        row = node("row", [0, 1800, 1080, 400], cls="android.view.ViewGroup")
        self.assertEqual(partially_visible([ROOT, row], SCREEN), [])

    def test_clipped_by_smaller_container(self):
        pager = node("pager", [0, 300, 1080, 1500], cls="android.view.ViewGroup")
        row = node("row", [0, 1700, 1080, 400], parent="pager", cls="android.view.ViewGroup")
        c = partially_visible([ROOT, pager, row], SCREEN)
        self.assertEqual([x["node_ids"] for x in c], [["row"]])

    def test_only_outermost_flagged(self):
        row = node("row", [0, 2300, 1080, 400], cls="android.view.ViewGroup")
        img = node("img", [0, 2300, 500, 400], parent="row", cls="android.widget.ImageView")
        self.assertEqual([x["node_ids"] for x in partially_visible([ROOT, row, img], SCREEN)], [["row"]])

    def test_still_clipped_after_swipe(self):
        row = node("row", [0, 2300, 1080, 400], cls="android.view.ViewGroup")
        c = still_clipped([ROOT, row], [ROOT, dict(row)], SCREEN)
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["check"], "clipped")
        self.assertEqual(c[0]["hint"], "high")

    def test_scrolled_into_view_not_clipped(self):
        row = node("row", [0, 2300, 1080, 400], cls="android.view.ViewGroup")
        moved = dict(row, bounds=[0, 1700, 1080, 400])
        self.assertEqual(still_clipped([ROOT, row], [ROOT, moved], SCREEN), [])


class TextChecksTest(unittest.TestCase):
    def test_truncation_long_menu_item(self):
        # peers: content-sized boxes ≈ 21 px/char; the cut item has only 15.6 px/char
        peers = [node("p1", [40, 600, 230, 56], text="Импорт мест"),
                 node("p2", [40, 700, 190, 56], text="Настройки"),
                 node("p3", [40, 800, 235, 56], text="Сохранённые")]
        long_ = node("a", [40, 500, 420, 56], text="Импорт из текста или ссылки")
        c = truncation_candidates([ROOT, long_] + peers)
        self.assertEqual([x["node_ids"] for x in c], [["a"]])

    def test_truncation_short_item_ok(self):
        short = node("b", [40, 600, 420, 56], text="Импорт мест")
        self.assertEqual(truncation_candidates([ROOT, short]), [])

    def test_truncation_quiet_on_real_settings_screen(self):
        from hierarchy import normalize
        from tests.fresh_eyes.helpers import FIXTURES
        import os
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as f:
            nodes = normalize(f.read(), "uiautomator")
        self.assertEqual(truncation_candidates(nodes), [])

    def test_repeated_disclaimer_in_cards(self):
        nodes = [ROOT] + [node("t%d" % i, [40, 300 + i * 400, 300, 50], text="туда-обратно") for i in range(4)]
        c = repeated_strings(nodes)
        self.assertEqual(len(c), 1)
        self.assertIn("4", c[0]["detail"])
        self.assertEqual(len(c[0]["node_ids"]), 4)

    def test_repeated_ignores_case_and_spaces(self):
        texts = ["Нет цен на  твои даты", "нет цен на твои даты", "НЕТ ЦЕН НА ТВОИ ДАТЫ"]
        nodes = [ROOT] + [node("t%d" % i, [40, 300 + i * 400, 300, 50], text=t) for i, t in enumerate(texts)]
        self.assertEqual(len(repeated_strings(nodes)), 1)

    def test_short_repeated_ignored(self):
        nodes = [ROOT] + [node("s%d" % i, [40, 300 + i * 200, 100, 50], text="Share") for i in range(5)]
        self.assertEqual(repeated_strings(nodes), [])

    def test_overlap_siblings(self):
        a = node("a", [100, 100, 200, 50], text="Hello")
        b = node("b", [150, 100, 200, 50], text="World")
        c = overlaps([ROOT, a, b])
        self.assertEqual(len(c), 1)
        self.assertEqual(sorted(c[0]["node_ids"]), ["a", "b"])
        self.assertEqual(c[0]["bbox"], [150, 100, 150, 50])

    def test_keyboard_missing_only_when_flag_set_and_no_keyboard(self):
        field = node("f", [40, 600, 1000, 120], text="Город", focused=True, cls="android.widget.EditText")
        ctx = {"screen_wh": SCREEN, "px_per_dp": 2.625, "min_dp": 48, "keyboard_top": None, "keyboard": True}
        got = [c for c in run_all([ROOT, field], ctx) if c["check"] == "keyboard_missing"]
        self.assertEqual([c["node_ids"] for c in got], [["f"]])
        self.assertEqual([c for c in run_all([ROOT, field], dict(ctx, keyboard_top=1500))
                          if c["check"] == "keyboard_missing"], [])
        self.assertEqual([c for c in run_all([ROOT, field], dict(ctx, keyboard=False))
                          if c["check"] == "keyboard_missing"], [])

    def test_content_under_ios_native_bar_ignored_header_view_checked(self):
        bar = node("bar", [0, 2300, 1080, 100], cls="Toolbar", label="Панель инструментов")
        field = node("f", [40, 2310, 900, 80], parent="bar", cls="SearchField", label="Поиск")
        row = node("row", [0, 2280, 1080, 120], text="Экранное время", cls="StaticText")
        self.assertEqual(overlaps([ROOT, bar, field, row]), [])
        header = node("h", [0, 2300, 1080, 100], cls="Other", label="Шапка")
        self.assertEqual(len(overlaps([ROOT, header, row])), 1)

    def test_layer_over_layer_is_not_an_overlap_but_header_over_list_is(self):
        lst = node("list", [0, 0, 1080, 2400], cls="ScrollView")
        row = node("row", [0, 200, 1080, 150], parent="list", text="Основные", cls="StaticText")
        search = node("search", [0, 0, 1080, 2400], cls="Other")
        sug = node("sug", [0, 220, 1080, 150], parent="search", text="Предложения", cls="StaticText")
        self.assertEqual(overlaps([ROOT, lst, row, search, sug], screen_wh=SCREEN), [])
        header = node("h", [0, 180, 1080, 200], cls="Other", label="Шапка")
        self.assertEqual(len(overlaps([ROOT, lst, row, header], screen_wh=SCREEN)), 1)

    def test_small_title_inside_its_own_cell_is_not_a_target(self):
        cell = node("cell", [0, 400, 1080, 160], cls="Cell", label="Основные", clickable=True)
        title = node("t", [40, 440, 300, 80], parent="cell", cls="Button", label="Основные", clickable=True)
        heart = node("heart", [900, 440, 50, 50], parent="cell", cls="Button", label="Нравится", clickable=True)
        got = [c["node_ids"] for c in small_targets([ROOT, cell, title, heart], 2.625, 48)]
        self.assertEqual(got, [["heart"]])

    def test_one_element_overlapping_many_is_one_candidate(self):
        big = node("big", [0, 0, 1080, 1800], text="Аккаунт Apple и очень длинный текст")
        rows = [node("r%d" % i, [0, 200 + 300 * i, 1080, 150], text="Строка %d" % i) for i in range(5)]
        got = overlaps([ROOT, big] + rows)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["node_ids"][0], "big")
        self.assertIn("5 elements", got[0]["detail"])

    def test_overlap_parent_child_ignored(self):
        a = node("a", [100, 100, 200, 50], label="card")
        b = node("b", [110, 100, 150, 50], parent="a", text="Title")
        self.assertEqual(overlaps([ROOT, a, b]), [])


class TouchAndKeyboardTest(unittest.TestCase):
    def test_small_target(self):
        btn = node("btn", [500, 500, 30, 30], clickable=True, cls="android.widget.ImageButton")
        self.assertEqual([x["node_ids"] for x in small_targets([ROOT, btn], 2.625, 48)], [["btn"]])

    def test_big_target_ok(self):
        btn = node("btn", [500, 500, 140, 140], clickable=True)
        self.assertEqual(small_targets([ROOT, btn], 2.625, 48), [])

    def test_suggestion_under_keyboard(self):
        field = node("f", [40, 1100, 1000, 120], cls="android.widget.EditText")
        before = [ROOT, field]
        after = [ROOT, dict(field, focused=True), node("s1", [40, 1940, 1000, 120], text="Lisbon, Portugal", clickable=True)]
        c = under_keyboard(after, 1500, before)
        self.assertEqual([x["node_ids"] for x in c], [["s1"]])

    def test_focused_field_under_keyboard(self):
        field = node("f", [40, 1600, 1000, 120], cls="android.widget.EditText", focused=True)
        self.assertEqual([x["node_ids"] for x in under_keyboard([ROOT, field], 1500)], [["f"]])

    def test_no_keyboard_no_candidates(self):
        field = node("f", [40, 1600, 1000, 120], focused=True)
        self.assertEqual(under_keyboard([ROOT, field], None), [])


class RunAllTest(unittest.TestCase):
    def test_run_all_ignores_status_bar(self):
        clock = node("clock", [11, 2, 115, 58], text="8:57", clickable=True, rid="com.android.systemui:id/clock")
        ctx = {"screen_wh": SCREEN, "px_per_dp": 2.625, "min_dp": 48, "keyboard_top": None}
        self.assertEqual(run_all([ROOT, clock], ctx), [])

    def test_run_all_combines_checks(self):
        row = node("row", [0, 2300, 1080, 400], cls="android.view.ViewGroup")
        btn = node("btn", [500, 500, 30, 30], clickable=True)
        ctx = {"screen_wh": SCREEN, "px_per_dp": 2.625, "min_dp": 48, "keyboard_top": None}
        checks = sorted(x["check"] for x in run_all([ROOT, row, btn], ctx))
        self.assertEqual(checks, ["partially_visible", "small_target"])


if __name__ == "__main__":
    unittest.main()
