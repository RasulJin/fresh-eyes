import os
import shutil
import sys
import unittest
from unittest import mock

from tests.fresh_eyes.helpers import FIXTURES, make_png, tmpdir
import capture
import devices
from fe_common import read_json, run
from hierarchy import HierarchyError


def fake_screenshot(dev, dst):
    shutil.copyfile(os.path.join(FIXTURES, "settings.png"), dst)
    return dst


class SnapTest(unittest.TestCase):
    def setUp(self):
        for name, fn in {"screenshot": fake_screenshot, "px_per_dp": lambda d: 2.625,
                         "keyboard_top": lambda d: None}.items():
            p = mock.patch.object(devices, name, fn)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(capture.time, "sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)

    def test_snap_writes_full_pack(self):
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as f:
            xml = f.read()
        with tmpdir() as d, mock.patch.object(capture, "_dump_uiautomator", lambda dev: xml):
            pack = os.path.join(d, "android", "settings", "default-none")
            code = capture.snap("android", "emulator-5560", pack)
            self.assertEqual(code, 0)
            for name in ("screen.png", "screen_1000.png", "nodes.json", "hierarchy.txt", "candidates.json",
                         "som.png", "som.json", "meta.json"):
                self.assertTrue(os.path.exists(os.path.join(pack, name)), name)
            meta = read_json(os.path.join(pack, "meta.json"))
            self.assertEqual(meta["screen_wh"], [1080, 2400])
            self.assertNotIn("blocked", meta)
            self.assertIsInstance(read_json(os.path.join(pack, "candidates.json")), list)
            self.assertGreater(len(read_json(os.path.join(pack, "som.json"))), 0)

    def test_snap_crops_each_candidate(self):
        xml = ('<hierarchy><node bounds="[0,0][1080,2400]" class="F"><node bounds="[0,2300][1080,2700]" '
               'class="android.view.ViewGroup" text="" content-desc="" resource-id="" clickable="false"/>'
               '</node></hierarchy>')
        with tmpdir() as d, mock.patch.object(capture, "_dump_uiautomator", lambda dev: xml):
            pack = os.path.join(d, "p")
            capture.snap("android", "emulator-5560", pack)
            crops = os.listdir(os.path.join(pack, "crops"))
            self.assertEqual(crops, ["partially_visible-1.png"])

    def test_snap_blocked_on_empty_hierarchy(self):
        def boom(dev):
            raise HierarchyError("could not get idle state")
        with tmpdir() as d, mock.patch.object(capture, "_dump_uiautomator", boom):
            pack = os.path.join(d, "p")
            self.assertEqual(capture.snap("android", "emulator-5560", pack), 3)
            meta = read_json(os.path.join(pack, "meta.json"))
            self.assertIn("hierarchy unavailable", meta["blocked"])
            self.assertTrue(os.path.exists(os.path.join(pack, "screen.png")))

    def test_snap_ios_needs_hierarchy_file(self):
        with tmpdir() as d:
            self.assertEqual(capture.snap("ios", "AAA", os.path.join(d, "p")), 3)


class AroundTest(unittest.TestCase):
    def test_around_records_and_analyzes(self):
        with tmpdir() as d:
            src = os.path.join(d, "src.mkv")
            run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=90x160:d=1:r=30",
                 "-vf", "drawbox=x=10:y=10:w=60:h=60:color=black:t=fill:enable='eq(n,15)'", "-c:v", "ffv1", src])

            class Rec(object):
                started = 0.0

            def start(dev, dst, limit_s=60):
                r = Rec()
                r.dst = dst
                return r

            def stop(rec):
                shutil.copyfile(src, rec.dst)
                return rec.dst

            pack = os.path.join(d, "p")
            with mock.patch.object(devices, "start_recording", start), \
                    mock.patch.object(devices, "stop_recording", stop), \
                    mock.patch.object(capture.time, "sleep", lambda s: None):
                code = capture.around("android", "emulator-5560", pack, [sys.executable, "-c", "pass"], settle=0)
            self.assertEqual(code, 0)
            motion = read_json(os.path.join(pack, "motion.json"))
            self.assertIn("flicker", [f["type"] for f in motion["flags"]])
            meta = read_json(os.path.join(pack, "around.json"))
            self.assertEqual(meta["action_exit"], 0)
            self.assertIn("record_s", meta)


if __name__ == "__main__":
    unittest.main()


class SnapCornerTest(unittest.TestCase):
    def test_snap_adds_sharp_corner_candidate_with_corner_crops(self):
        from tests.fresh_eyes.helpers import make_png

        def shot(dev, dst):
            return make_png(dst, 300, 600, bg=(60, 60, 60), rects=[(0, 0, 200, 600, (250, 250, 250), 0)])

        xml = ('<hierarchy><node bounds="[0,0][300,600]" class="F"><node bounds="[0,0][200,600]" '
               'class="android.view.ViewGroup" text="" content-desc="" resource-id="" clickable="false"/>'
               '</node></hierarchy>')
        with tmpdir() as d, mock.patch.object(devices, "screenshot", shot), \
                mock.patch.object(devices, "px_per_dp", lambda dv: 2.0), \
                mock.patch.object(devices, "keyboard_top", lambda dv: None), \
                mock.patch.object(capture, "_dump_uiautomator", lambda dev: xml):
            pack = os.path.join(d, "p")
            capture.snap("android", "emulator-5560", pack)
            cands = read_json(os.path.join(pack, "candidates.json"))
            self.assertIn("sharp_corners", [c["check"] for c in cands])
            crops = sorted(os.listdir(os.path.join(pack, "crops")))
            self.assertIn("sharp_corners-1-tr.png", crops)
            self.assertIn("sharp_corners-1-br.png", crops)


class ExpectDiffersTest(unittest.TestCase):
    def test_identical_screen_to_reference_pack_is_rejected(self):
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as fh:
            xml = fh.read()
        with tmpdir() as d, mock.patch.object(devices, "screenshot", fake_screenshot), \
                mock.patch.object(devices, "px_per_dp", lambda dv: 2.625), \
                mock.patch.object(devices, "keyboard_top", lambda dv: None), \
                mock.patch.object(capture, "_dump_uiautomator", lambda dev: xml), \
                mock.patch.object(capture.time, "sleep", lambda s: None):
            base = os.path.join(d, "default-none")
            self.assertEqual(capture.snap("android", "emulator-5560", base), 0)
            dark = os.path.join(d, "dark-none")
            self.assertEqual(capture.snap("android", "emulator-5560", dark, expect_differs=base), 4)
            self.assertIn("identical", read_json(os.path.join(dark, "meta.json"))["blocked"])


class StaleCropsTest(unittest.TestCase):
    def test_resnap_removes_old_crops(self):
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as fh:
            xml = fh.read()
        with tmpdir() as d, mock.patch.object(devices, "screenshot", fake_screenshot), \
                mock.patch.object(devices, "px_per_dp", lambda dv: 2.625), \
                mock.patch.object(devices, "keyboard_top", lambda dv: None), \
                mock.patch.object(capture, "_dump_uiautomator", lambda dev: xml):
            pack = os.path.join(d, "p")
            os.makedirs(os.path.join(pack, "crops"))
            open(os.path.join(pack, "crops", "truncation-7.png"), "w").close()
            capture.snap("android", "emulator-5560", pack)
            self.assertFalse(os.path.exists(os.path.join(pack, "crops", "truncation-7.png")))
