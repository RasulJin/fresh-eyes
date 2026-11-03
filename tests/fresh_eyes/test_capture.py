import json
import os
import shutil
import sys
import unittest
from unittest import mock

from tests.fresh_eyes.helpers import FIXTURES, make_png, tmpdir
import capture
import devices
import nav
from fe_common import read_json, run
from hierarchy import HierarchyError


def fake_screenshot(dev, dst):
    shutil.copyfile(os.path.join(FIXTURES, "settings.png"), dst)
    return dst


class SnapTest(unittest.TestCase):
    def setUp(self):
        for name, fn in {"screenshot": fake_screenshot, "px_per_dp": lambda d: 2.625,
                         "keyboard_top": lambda d, n=(): None}.items():
            p = mock.patch.object(devices, name, fn)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(capture.time, "sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)

    def test_snap_writes_full_pack(self):
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as f:
            xml = f.read()
        with tmpdir() as d, mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml):
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

    def test_snap_after_a_gesture_records_its_before_pack_and_what_moved(self):
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as f:
            xml = f.read()
        with tmpdir() as d, mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml):
            first = os.path.join(d, "android", "settings", "default-swipe-inside-before")
            after = os.path.join(d, "android", "settings", "default-swipe-inside")
            capture.snap("android", "emulator-5560", first)
            capture.snap("android", "emulator-5560", after, before=first)
            meta = read_json(os.path.join(after, "meta.json"))
        self.assertEqual((meta["before"], meta["moved"]), (first, 0))

    def test_every_snap_records_whether_the_keyboard_is_up(self):
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as f:
            xml = f.read()
        with tmpdir() as d, mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml), \
                mock.patch.object(devices, "keyboard_top", lambda dev, n=(): 1500):
            pack = os.path.join(d, "android", "settings", "default-none")
            self.assertEqual(capture.snap("android", "emulator-5560", pack), 0)  # no --keyboard
            meta = read_json(os.path.join(pack, "meta.json"))
        self.assertTrue(meta["keyboard_up"])
        self.assertIsNone(meta["keyboard_top"])  # geometry still checks the keyboard only on --keyboard snaps

    def test_snap_crops_each_candidate(self):
        xml = ('<hierarchy><node bounds="[0,0][1080,2400]" class="F"><node bounds="[0,2300][1080,2700]" '
               'class="android.view.ViewGroup" text="" content-desc="" resource-id="" clickable="false"/>'
               '</node></hierarchy>')
        with tmpdir() as d, mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml):
            pack = os.path.join(d, "p")
            capture.snap("android", "emulator-5560", pack)
            crops = os.listdir(os.path.join(pack, "crops"))
            self.assertEqual(crops, ["partially_visible-1.png"])

    def test_snap_blocked_on_empty_hierarchy(self):
        def boom(dev):
            raise HierarchyError("could not get idle state")
        with tmpdir() as d, mock.patch.object(nav, "_dump_uiautomator", boom):
            pack = os.path.join(d, "p")
            self.assertEqual(capture.snap("android", "emulator-5560", pack), 3)
            meta = read_json(os.path.join(pack, "meta.json"))
            self.assertIn("hierarchy unavailable", meta["blocked"])
            self.assertTrue(os.path.exists(os.path.join(pack, "screen.png")))

    def test_snap_ios_without_driver_is_blocked(self):
        def no_driver(dev):
            raise devices.FEError("no Maestro iOS driver for AAA")
        with tmpdir() as d, mock.patch.object(devices, "ios_driver", no_driver):
            pack = os.path.join(d, "p")
            self.assertEqual(capture.snap("ios", "AAA", pack), 3)
            self.assertIn("no Maestro iOS driver", read_json(os.path.join(pack, "meta.json"))["blocked"])


class CropOrderTest(unittest.TestCase):
    def test_round_robin_most_severe_first(self):
        cands = [{"check": "overlap", "hint": "medium"} for _ in range(20)] + \
                [{"check": "small_target", "hint": "low"}, {"check": "clipped", "hint": "high"}]
        first = [c["check"] for c in capture._crop_order(cands)[:3]]
        self.assertEqual(first, ["clipped", "overlap", "small_target"])


class IosSnapTest(unittest.TestCase):
    def test_reads_driver_and_works_in_pixels(self):
        from tests.fresh_eyes.test_hierarchy import XCTEST
        calls = []

        def call(dev, route, body=None, timeout=20, base=None):
            calls.append(route)
            if route == "deviceInfo":  # settings.png is 1080x2400
                return json.dumps({"widthPoints": 360, "heightPoints": 800, "widthPixels": 1080, "heightPixels": 2400})
            return json.dumps(XCTEST)
        with tmpdir() as d, mock.patch.object(devices, "screenshot", fake_screenshot), \
                mock.patch.object(devices, "ios_driver", lambda dev: "http://127.0.0.1:22087"), \
                mock.patch.object(devices, "ios_call", call):
            pack = os.path.join(d, "p")
            self.assertEqual(capture.snap("ios", "AAA", pack, keyboard=True), 0)
            nodes = read_json(os.path.join(pack, "nodes.json"))
            meta = read_json(os.path.join(pack, "meta.json"))
        self.assertIn("viewHierarchy", calls)
        cam = next(n for n in nodes if n["rid"] == "com.apple.settings.camera")
        self.assertEqual(cam["bounds"], [48, 1300, 1110, 156])
        self.assertEqual(meta["px_per_dp"], 3.0)
        self.assertEqual(meta["keyboard_top"], 1614)


class LogsTest(unittest.TestCase):
    def test_keeps_the_apps_lines_and_crashes_and_collapses_floods(self):
        logcat = "\n".join(
            ["W/Reanimated( 4242): synchronouslyUpdateUIProps failed for tag %d" % i for i in range(5000)] +
            ["E/ReactNativeJS( 4242): TypeError: undefined is not an object",
             "W/chatty( 999): other app noise",
             "E/AndroidRuntime( 5001): FATAL EXCEPTION: main",
             "E/AndroidRuntime( 5001): Process: com.example.app, PID: 5001",
             "E/AndroidRuntime( 7000): java.lang.IllegalStateException: UiAutomationService already registered",
             "E/AndroidRuntime( 7000): Process: com.android.commands.uiautomator, PID: 7000",
             "I/ReactNativeJS( 4242): info is below the W filter anyway"])
        calls = []

        def adb(dev_id, *args, **kw):
            calls.append(args)
            return "4242\n" if args[:2] == ("shell", "pidof") else logcat
        with tmpdir() as d, mock.patch.object(devices, "_adb", adb):
            self.assertEqual(capture.logs("android", "emulator-5560", d, "com.example.app"), 0)
            with open(os.path.join(d, "logcat.txt"), encoding="utf-8") as f:
                lines = f.read().splitlines()
            self.assertEqual(capture.logs("android", "emulator-5560", d, "com.example.app", clear=True), 0)
        self.assertIn("5003 warnings/errors, 4 distinct", lines[0])  # the uiautomator crash is not the app's
        self.assertEqual([l.split(" x ")[1][:16] for l in lines[1:]],
                         ["E/AndroidRuntime", "E/AndroidRuntime", "E/ReactNativeJS:", "W/Reanimated: sy"])
        self.assertTrue(lines[4].startswith("  5000 x "))
        self.assertEqual(calls[-1], ("logcat", "-c"))


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
                mock.patch.object(devices, "keyboard_top", lambda dv, n=(): None), \
                mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml):
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
                mock.patch.object(devices, "keyboard_top", lambda dv, n=(): None), \
                mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml), \
                mock.patch.object(capture.time, "sleep", lambda s: None):
            base = os.path.join(d, "default-none")
            self.assertEqual(capture.snap("android", "emulator-5560", base), 0)
            dark = os.path.join(d, "dark-none")
            self.assertEqual(capture.snap("android", "emulator-5560", dark, expect_differs=base), 4)
            self.assertIn("did not apply", read_json(os.path.join(dark, "meta.json"))["blocked"])

    def test_clock_tick_is_not_a_state_change_theme_is(self):
        from pixels import changed_fraction
        with tmpdir() as d:
            light = os.path.join(d, "light.png")
            make_png(light, 360, 800, rects=[(0, 0, 360, 40, (240, 240, 240), 0), (20, 100, 320, 60, (30, 30, 30), 8)])
            tick = os.path.join(d, "tick.png")  # only the status bar clock changed
            make_png(tick, 360, 800, rects=[(0, 0, 360, 40, (240, 240, 240), 0), (10, 10, 40, 20, (0, 0, 0), 0),
                                            (20, 100, 320, 60, (30, 30, 30), 8)])
            dark = os.path.join(d, "dark.png")
            make_png(dark, 360, 800, bg=(18, 18, 18), rects=[(20, 100, 320, 60, (230, 230, 230), 8)])
            self.assertLess(changed_fraction(light, tick), 0.03)
            self.assertGreater(changed_fraction(light, dark), 0.5)


class StaleCropsTest(unittest.TestCase):
    def test_resnap_removes_old_crops(self):
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as fh:
            xml = fh.read()
        with tmpdir() as d, mock.patch.object(devices, "screenshot", fake_screenshot), \
                mock.patch.object(devices, "px_per_dp", lambda dv: 2.625), \
                mock.patch.object(devices, "keyboard_top", lambda dv, n=(): None), \
                mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml):
            pack = os.path.join(d, "p")
            os.makedirs(os.path.join(pack, "crops"))
            open(os.path.join(pack, "crops", "truncation-7.png"), "w").close()
            capture.snap("android", "emulator-5560", pack)
            self.assertFalse(os.path.exists(os.path.join(pack, "crops", "truncation-7.png")))


class OrderTest(unittest.TestCase):
    def test_screenshot_taken_after_hierarchy_settles(self):
        order = []
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as fh:
            xml = fh.read()

        def shot(dev, dst):
            order.append("screenshot")
            return fake_screenshot(dev, dst)

        def dump(dev):
            order.append("hierarchy")
            return xml

        with tmpdir() as d, mock.patch.object(devices, "screenshot", shot), \
                mock.patch.object(devices, "px_per_dp", lambda dv: 2.625), \
                mock.patch.object(devices, "keyboard_top", lambda dv, n=(): None), \
                mock.patch.object(nav, "_dump_uiautomator", dump):
            capture.snap("android", "emulator-5560", os.path.join(d, "p"))
        self.assertEqual(order[:2], ["hierarchy", "screenshot"])


if __name__ == "__main__":
    unittest.main()


def _node(i, cls, bounds, depth=1, text=""):
    return {"id": "n%d" % i, "parent": None, "depth": depth, "cls": cls, "text": text, "label": "", "rid": "",
            "bounds": bounds, "clickable": False, "focused": False, "enabled": True}


class PagesTest(unittest.TestCase):
    def test_main_list_is_the_innermost_tall_vertical_scroll(self):
        nodes = [_node(0, "android.widget.ScrollView", [0, 0, 1080, 2400], 0),
                 _node(1, "android.widget.ScrollView", [0, 63, 1080, 2085], 2),
                 _node(2, "android.widget.HorizontalScrollView", [0, 0, 1080, 2400], 4),
                 _node(3, "android.widget.ScrollView", [0, 300, 1080, 300], 5)]
        self.assertEqual(capture.main_list(nodes, 2400)["id"], "n1")
        self.assertIsNone(capture.main_list(nodes[3:], 2400))

    def test_scrolls_page_by_page_to_the_end_and_back_to_the_top(self):
        W, H, CONTENT, SLOP = 360, 800, 1800, 8  # px_per_dp 1: the list eats 8 px of each drag
        state = {"off": 0, "pulled": 0}  # pulled: px dragged past the top (pull-to-refresh)
        rows = range(100, CONTENT, 200)

        def visible():
            return [(k, y - state["off"]) for k, y in enumerate(rows) if 0 < y - state["off"] < H - 100]

        def frame(dst):  # a sticky header that never moves, rows that do
            make_png(dst, W, H, rects=[(0, 0, W, 60, (200, 200, 200), 0)] +
                     [(20, y, 320, 100, (40, 40, 40), 8) for _, y in visible()])
            return dst

        def fake_snap(platform, device_id, out, *a, **kw):
            os.makedirs(out, exist_ok=True)
            frame(os.path.join(out, "screen.png"))
            nodes = [_node(0, "android.widget.ScrollView", [0, 0, W, H], 1),
                     _node(1, "android.widget.TextView", [0, 10, 100, 40], 2, "Back"),
                     _node(2, "android.widget.TextView", [100, 10, 160, 40], 2, "Header"),
                     _node(3, "android.widget.TextView", [260, 10, 100, 40], 2, "Share")]
            nodes += [_node(4 + k, "android.widget.TextView", [20, y, 320, 100], 2, "row %d" % k)
                      for k, y in visible()]
            with open(os.path.join(out, "nodes.json"), "w") as f:
                json.dump(nodes, f)
            with open(os.path.join(out, "meta.json"), "w") as f:
                json.dump({"screen_wh": [W, H], "px_per_dp": 1.0}, f)
            return 0

        def swipe(dev, x1, y1, x2, y2, ms=300):
            d = y1 - y2
            step = (abs(d) - SLOP) * (1 if d > 0 else -1)
            state["pulled"] = max(state["pulled"], -(state["off"] + step))
            state["off"] = max(0, min(CONTENT - H, state["off"] + step))

        with tmpdir() as d, mock.patch.object(capture, "snap", fake_snap), \
                mock.patch.object(devices, "swipe", swipe), \
                mock.patch.object(devices, "screenshot", lambda dev, dst: frame(dst)), \
                mock.patch.object(capture.time, "sleep", lambda s: None):
            pack = os.path.join(d, "default-none")
            fake_snap("android", "emulator-5560", pack)
            meta = capture.pages("android", "emulator-5560", pack)
            self.assertEqual((meta["scroll_pages"], meta["scroll_restored"]), (3, True))
            self.assertEqual((state["off"], state["pulled"]), (0, 0))  # the same distances back: no pull past the top
            self.assertEqual(sorted(n for n in os.listdir(pack) if n.startswith("scroll-")),
                             ["scroll-1", "scroll-2", "scroll-3"])
            self.assertEqual(read_json(os.path.join(pack, "meta.json"))["scroll_pages"], 3)
            self.assertFalse(os.path.exists(os.path.join(pack, ".top.png")))

    def test_a_screen_without_a_list_gets_no_pages(self):
        with tmpdir() as d, mock.patch.object(devices, "screenshot", fake_screenshot):
            pack = os.path.join(d, "p")
            os.makedirs(pack)
            shutil.copyfile(os.path.join(FIXTURES, "settings.png"), os.path.join(pack, "screen.png"))
            with open(os.path.join(pack, "nodes.json"), "w") as f:
                json.dump([_node(0, "android.widget.FrameLayout", [0, 0, 1080, 2400])], f)
            with open(os.path.join(pack, "meta.json"), "w") as f:
                json.dump({"screen_wh": [1080, 2400]}, f)
            meta = capture.pages("android", "emulator-5560", pack)
        self.assertEqual((meta["scroll_pages"], meta["scroll_restored"]), (0, True))
