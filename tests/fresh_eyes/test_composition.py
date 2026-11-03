import os
import unittest

from tests.fresh_eyes.helpers import make_png, tmpdir
from composition import blank_gaps, crowded, gray_dp

BLACK, CORAL = (0, 0, 0), (226, 116, 72)
W, H = 400, 900  # 1 px per dp


def text_line(y):
    """A row of glyph-like strokes: many edges, like a line of text."""
    return [(20 + 12 * i, y, 5, 20, BLACK, 0) for i in range(25)]


def node(i, bounds, cls="android.view.ViewGroup", clickable=False, parent=None, text=""):
    return {"id": i, "parent": parent, "cls": cls, "text": text, "label": "", "rid": "", "bounds": bounds,
            "clickable": clickable, "focused": False, "enabled": True}


class BlankGapTest(unittest.TestCase):
    def gaps(self, rects, nodes=(), kb=None):
        with tmpdir() as d:
            p = make_png(os.path.join(d, "s.png"), W, H, rects=rects)
            return blank_gaps(gray_dp(p, 1.0), list(nodes), 1.0, kb)

    def test_a_wide_blank_band_between_content_is_a_candidate(self):
        c = self.gaps(text_line(100) + text_line(300))
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["check"], "blank_gap")
        self.assertEqual(c[0]["bbox"][1], 120)
        self.assertIn("180 dp", c[0]["detail"])
        self.assertEqual(c[0]["hint"], "medium")

    def test_normal_section_spacing_is_not(self):
        self.assertEqual(self.gaps(text_line(100) + text_line(160)), [])

    def test_space_above_a_bar_pinned_at_the_bottom_is_not(self):
        self.assertEqual(self.gaps(text_line(100) + text_line(H - 140)), [])

    def test_space_above_a_keyboard_pinned_input_is_not(self):
        self.assertEqual(self.gaps(text_line(100) + text_line(420), kb=500), [])

    def test_the_empty_inside_of_an_input_is_not(self):
        field = node(1, [10, 110, 380, 200], cls="android.widget.EditText", clickable=True)
        self.assertEqual(self.gaps(text_line(100) + text_line(300), [field]), [])

    def test_pale_boxes_the_hierarchy_knows_are_not(self):
        tile = node(1, [20, 150, 120, 120])  # a skeleton tile too pale to make edges
        self.assertEqual(self.gaps(text_line(100) + text_line(300), [tile]), [])


class CrowdedTest(unittest.TestCase):
    def pair(self, gap, joined=False):
        """Two 40 dp buttons; their bounds carry 6 dp of padding for the hit area, so the bounds overlap."""
        x2 = 20 + 150 + gap
        rects = [(20, 100, 150, 40, CORAL, 8), (x2, 100, 150, 40, CORAL, 8)]
        if joined:
            rects.append((160, 100, x2 - 140, 40, CORAL, 0))
        nodes = [node(1, [14, 94, 162, 52], clickable=True, text="Import"),
                 node(2, [x2 - 6, 94, 162, 52], clickable=True, text="Create")]
        with tmpdir() as d:
            p = make_png(os.path.join(d, "s.png"), W, H, rects=rects)
            return crowded(gray_dp(p, 1.0), nodes, 1.0)

    def test_pills_drawn_4dp_apart_are_crowded(self):
        c = self.pair(4)
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["node_ids"], [1, 2])
        self.assertIn("'Import' and 'Create' drawn 4 dp apart", c[0]["detail"])

    def test_pills_8dp_apart_are_not(self):
        self.assertEqual(self.pair(8), [])

    def test_one_drawn_group_is_not(self):
        self.assertEqual(self.pair(4, joined=True), [])

    def test_a_tile_cut_by_its_scroll_is_not(self):
        rects = [(20, 860, 150, 40, CORAL, 0), (174, 860, 150, 40, CORAL, 0)]
        scroll = node(0, [0, 100, 400, 800], cls="android.widget.ScrollView")
        nodes = [scroll, node(1, [20, 860, 150, 40], clickable=True, parent=0),
                 node(2, [174, 860, 150, 40], clickable=True, parent=0)]
        with tmpdir() as d:
            p = make_png(os.path.join(d, "s.png"), W, H, rects=rects)
            self.assertEqual(crowded(gray_dp(p, 1.0), nodes, 1.0), [])


if __name__ == "__main__":
    unittest.main()
