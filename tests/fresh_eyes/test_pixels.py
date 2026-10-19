import os
import unittest

from tests.fresh_eyes.helpers import make_png, tmpdir
from fe_common import image_size, read_rgb
from pixels import corner_radius, crop, downscale, side_by_side, som_overlay, tile

BLACK = (0, 0, 0)


class PixelsTest(unittest.TestCase):
    def test_corner_radius_rounded(self):
        with tmpdir() as d:
            p = make_png(os.path.join(d, "r.png"), 200, 200, rects=[(20, 20, 160, 160, BLACK, 32)])
            for corner in ("tl", "tr", "bl", "br"):
                self.assertAlmostEqual(corner_radius(p, [20, 20, 160, 160], corner), 32, delta=4, msg=corner)

    def test_corner_radius_sharp(self):
        with tmpdir() as d:
            p = make_png(os.path.join(d, "s.png"), 200, 200, rects=[(20, 20, 160, 160, BLACK, 0)])
            self.assertLessEqual(corner_radius(p, [20, 20, 160, 160], "tr"), 3)

    def test_read_write_roundtrip(self):
        with tmpdir() as d:
            p = make_png(os.path.join(d, "x.png"), 10, 6, bg=(10, 20, 30))
            w, h, data = read_rgb(p)
            self.assertEqual((w, h), (10, 6))
            self.assertEqual(data[:3], bytes([10, 20, 30]))
            self.assertEqual(image_size(p), (10, 6))

    def test_crop_clamps_out_of_bounds(self):
        with tmpdir() as d:
            p = make_png(os.path.join(d, "x.png"), 100, 80)
            out = crop(p, os.path.join(d, "c.png"), [90, 70, 50, 50], pad=8)
            w, h = image_size(out)
            self.assertLessEqual(w, 100)
            self.assertLessEqual(h, 80)
            self.assertGreater(w, 0)

    def test_crop_zoom(self):
        with tmpdir() as d:
            p = make_png(os.path.join(d, "x.png"), 100, 80)
            out = crop(p, os.path.join(d, "c.png"), [10, 10, 20, 20], pad=0, zoom=4)
            self.assertEqual(image_size(out), (80, 80))

    def test_downscale_long_side(self):
        with tmpdir() as d:
            p = make_png(os.path.join(d, "big.png"), 1170, 2532)
            self.assertEqual(downscale(p, os.path.join(d, "s.png")), (462, 1000))

    def test_downscale_keeps_small(self):
        with tmpdir() as d:
            p = make_png(os.path.join(d, "small.png"), 300, 200)
            self.assertEqual(downscale(p, os.path.join(d, "s.png")), (300, 200))

    def test_side_by_side(self):
        with tmpdir() as d:
            a = make_png(os.path.join(d, "a.png"), 40, 40)
            b = make_png(os.path.join(d, "b.png"), 80, 40)
            out = side_by_side([a, b], os.path.join(d, "ab.png"), height=160)
            self.assertEqual(image_size(out), (160 + 320, 160))

    def test_tile_grid(self):
        with tmpdir() as d:
            frames = [make_png(os.path.join(d, "f%d.png" % i), 100, 200) for i in range(8)]
            out = tile(frames, os.path.join(d, "sheet.png"), cols=4, frame_h=300)
            self.assertEqual(image_size(out), (600, 600))

    def test_tile_partial_last_row(self):
        with tmpdir() as d:
            frames = [make_png(os.path.join(d, "f%d.png" % i), 100, 200) for i in range(5)]
            self.assertEqual(image_size(tile(frames, os.path.join(d, "sheet.png"), cols=4, frame_h=300)), (600, 600))

    def test_som_numbers_items_and_draws(self):
        with tmpdir() as d:
            p = make_png(os.path.join(d, "x.png"), 300, 300)
            items = [{"node_ids": ["n1"], "bbox": [10, 10, 50, 50]}, {"id": "n2", "bounds": [100, 100, 80, 40]},
                     {"node_ids": ["n3"], "bbox": [200, 200, 60, 60]}]
            out = os.path.join(d, "som.png")
            mapping = som_overlay(p, items, out)
            self.assertEqual([m["n"] for m in mapping], [1, 2, 3])
            self.assertEqual(mapping[1]["node_ids"], ["n2"])
            _, _, before = read_rgb(p)
            _, _, after = read_rgb(out)
            self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
