import contextlib
import io
import json
import os
import shutil
import sys
import time
import unittest
from unittest import mock

from tests.fresh_eyes.helpers import FIXTURES, make_png, tmpdir
import capture
import devices
import nav
from fe_common import read_json, run, write_json
from hierarchy import HierarchyError
from motion import DEFAULT_CEILING_MS


def _no_maestro(dev):
    raise HierarchyError("no Maestro CLI in tests")


_MAESTRO = mock.patch.object(nav, "_dump_maestro", _no_maestro)  # a test must never reach a real device


def setUpModule():
    _MAESTRO.start()


def tearDownModule():
    _MAESTRO.stop()


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
            self.assertGreater(meta["brightness"], 120)  # a light settings screen
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

    def test_snap_on_a_screen_without_the_marker_is_blocked(self):
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as f:
            xml = f.read()
        with tmpdir() as d, mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml):
            self.assertEqual(capture.snap("android", "emulator-5560", os.path.join(d, "a"), marker=["Лента"]), 5)
            self.assertIn("wrong screen", read_json(os.path.join(d, "a", "meta.json"))["blocked"])
            self.assertEqual(capture.snap("android", "emulator-5560", os.path.join(d, "b"),
                                          marker=["Лента", "network & internet"]), 0)

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

    def test_snap_records_how_it_read_the_hierarchy(self):
        from tests.fresh_eyes.test_hierarchy import MAESTRO
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as f:
            xml = f.read()
        with tmpdir() as d:
            with mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml):
                self.assertEqual(capture.snap("android", "emulator-5560", os.path.join(d, "a")), 0)
            with mock.patch.object(nav, "_dump_uiautomator", lambda dev: "ERROR: could not get idle state.\n"), \
                    mock.patch.object(nav, "_dump_maestro", lambda dev: json.dumps(MAESTRO)):
                self.assertEqual(capture.snap("android", "emulator-5560", os.path.join(d, "b")), 0)
            self.assertEqual([read_json(os.path.join(d, k, "meta.json"))["source"] for k in "ab"],
                             ["uiautomator", "maestro"])

    def test_a_blocked_resnap_keeps_no_file_of_the_older_screen(self):
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as f:
            xml = f.read()

        def boom(dev):
            raise HierarchyError("could not get idle state")
        with tmpdir() as d:
            pack = os.path.join(d, "p")
            with mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml):
                capture.snap("android", "emulator-5560", pack)
            with mock.patch.object(nav, "_dump_uiautomator", boom), \
                    mock.patch.object(nav, "_dump_maestro", boom):
                self.assertEqual(capture.snap("android", "emulator-5560", pack), 3)
            self.assertEqual(sorted(os.listdir(pack)), ["meta.json", "screen.png"])  # this moment's frame only
            self.assertNotIn("source", read_json(os.path.join(pack, "meta.json")))

    def test_a_recheck_snap_must_show_the_screen_of_its_pack(self):
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as f:
            xml = f.read()
        post = ('<hierarchy><node bounds="[0,0][1080,2400]" class="F" text="Пост" content-desc="" '
                'resource-id="" clickable="false"/></hierarchy>')
        with tmpdir() as run:
            write_json(os.path.join(run, "coverage.json"), {"cells": [
                {"id": "android/settings/default/none", "screen": "settings", "marker": ["Network & internet"]}]})
            pack = os.path.join(run, "android", "settings", "default-tap-everything", "tap-1")
            with mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml):
                capture.snap("android", "emulator-5560", pack)
                self.assertEqual(capture.snap("android", "emulator-5560", pack + "-recheck"), 0)
            with mock.patch.object(nav, "_dump_uiautomator", lambda dev: post):  # the thread recheck taken on Post
                self.assertEqual(capture.snap("android", "emulator-5560", pack + "-recheck"), 5)
                self.assertIn("wrong screen", read_json(os.path.join(pack + "-recheck", "meta.json"))["blocked"])
                self.assertEqual(capture.snap("android", "emulator-5560", pack + "-recheck-2"), 5)  # "times": 2
                self.assertEqual(capture.snap("android", "emulator-5560", pack + "-recheck/after"), 0)  # a tap's result
                other = os.path.join(run, "android", "settings", "default-tap-everything", "tap-2")
                capture.snap("android", "emulator-5560", other)  # a tap that opened another screen
                self.assertEqual(capture.snap("android", "emulator-5560", other + "-recheck"), 0)

    def _snap_with_app(self, pid):
        crash = "\n".join(["E/AndroidRuntime( 4242): FATAL EXCEPTION: main",
                           "E/AndroidRuntime( 4242): Process: com.example.app, PID: 4242",
                           "E/AndroidRuntime( 4242): java.lang.IllegalStateException: boom"])

        def adb(dev_id, *args, **kw):
            if args[:2] == ("shell", "pidof"):
                return pid
            return crash if "crash" in args else ""  # the crash buffer is read only when asked for
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as f:
            xml = f.read()
        with tmpdir() as d, mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml), \
                mock.patch.object(devices, "_adb", adb):
            pack = os.path.join(d, "p")
            self.assertEqual(capture.snap("android", "emulator-5560", pack, package="com.example.app"), 0)
            text = None
            if os.path.exists(os.path.join(pack, "logcat.txt")):
                with open(os.path.join(pack, "logcat.txt"), encoding="utf-8") as f:
                    text = f.read()
            return read_json(os.path.join(pack, "meta.json")), text

    def test_snap_of_a_dead_app_keeps_the_crash(self):
        meta, log = self._snap_with_app("")
        self.assertFalse(meta["app_running"])
        self.assertIn("java.lang.IllegalStateException: boom", log)

    def test_a_package_other_than_the_runs_app_is_refused_and_the_runs_is_the_default(self):
        # 0.5.12 feed tap-3: --package com.voiza.app read as app_running false, a false crash and a resnap
        pids = []

        def adb(dev_id, *args, **kw):
            pids.append(args[-1])
            return "4242\n"
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as f:
            xml = f.read()
        with tmpdir() as run, mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml), \
                mock.patch.object(devices, "_adb", adb):
            write_json(os.path.join(run, "coverage.json"), {"cells": []})
            write_json(os.path.join(run, "preflight.json"), {"platforms": {"android": {"app": "com.example.app"}}})
            pack = os.path.join(run, "android", "settings", "default-none")
            with self.assertRaises(capture.FEError):
                capture.snap("android", "emulator-5560", pack, package="com.exampel.app")
            self.assertEqual(capture.snap("android", "emulator-5560", pack), 0)
            self.assertEqual(pids, ["com.example.app"])
            self.assertTrue(read_json(os.path.join(pack, "meta.json"))["app_running"])

    def test_a_theme_pack_notes_the_phones_and_the_apps_theme(self):
        with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as f:
            xml = f.read()
        with tmpdir() as run, mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml), \
                mock.patch.object(devices, "night_mode", lambda dev: "Night mode: yes"):
            screen = os.path.join(run, "android", "trip-map")
            capture.snap("android", "e", os.path.join(screen, "theme-mismatch-none"), app_theme="Светлая")
            capture.snap("android", "e", os.path.join(screen, "dark-none"))
            capture.snap("android", "e", os.path.join(screen, "default-none"))
            self.assertEqual(read_json(os.path.join(screen, "theme-mismatch-none", "theme-note.json")),
                             {"ui_night_mode": "Night mode: yes", "app_theme": "Светлая"})
            self.assertIsNone(read_json(os.path.join(screen, "dark-none", "theme-note.json"))["app_theme"])
            self.assertFalse(os.path.exists(os.path.join(screen, "default-none", "theme-note.json")))

    def test_snap_of_a_running_app_writes_no_log(self):
        meta, log = self._snap_with_app("4242\n")
        self.assertTrue(meta["app_running"])
        self.assertIsNone(log)

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
        self.assertEqual(meta["source"], "xctest")


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
        self.assertEqual(calls[-1], ("logcat",) + capture.BUFFERS + ("-c",))

    def test_keeps_a_native_crash_that_names_the_app(self):
        logcat = "\n".join([
            "F/libc    ( 5001): Fatal signal 11 (SIGSEGV), code 1, fault addr 0x0 in tid 5020 (mqt_js), "
            "pid 5001 (com.example.app)",
            "E/libc    ( 5001): Abort message: 'JSI error'",
            "E/libc    ( 6001): Abort message: 'other app'",
            "F/libc    ( 6001): Fatal signal 6 (SIGABRT), pid 6001 (com.other.app)",
            "F/linker  ( 7001): CANNOT LINK EXECUTABLE"])

        def adb(dev_id, *args, **kw):
            return "" if args[:2] == ("shell", "pidof") else logcat
        with tmpdir() as d, mock.patch.object(devices, "_adb", adb):
            capture.logs("android", "emulator-5560", d, "com.example.app")
            with open(os.path.join(d, "logcat.txt"), encoding="utf-8") as f:
                text = f.read()
        self.assertIn("Fatal signal 11", text)
        self.assertIn("JSI error", text)  # the whole block, not only its F line
        self.assertNotIn("com.other.app", text)
        self.assertIn("CANNOT LINK EXECUTABLE", text)  # an F line outside a crash block is kept as is


class AroundTest(unittest.TestCase):
    def test_around_records_and_analyzes(self):
        with tmpdir() as d:
            src = os.path.join(d, "src.mkv")
            run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=90x160:d=1:r=30",
                 "-vf", "drawbox=x=10:y=10:w=60:h=60:color=black:t=fill:enable='eq(n,15)'", "-c:v", "ffv1", src])

            class Rec(object):
                def __init__(self):
                    self.started = time.time()

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
            self.assertTrue(meta["animated"])  # the black box covers ~25% of the frame

    def _ceiling(self, pack, **kw):
        seen = []
        rec = mock.Mock(started=time.time())
        with mock.patch.object(devices, "start_recording", lambda dev, dst, limit_s=60: rec), \
                mock.patch.object(devices, "stop_recording", lambda r: None), \
                mock.patch.object(capture, "analyze", lambda video, out, ceiling, *a: seen.append(ceiling) or
                                  {"flags": [], "peak_change": 0}), \
                mock.patch.object(capture.time, "sleep", lambda s: None):
            capture.around("android", "emulator-5560", pack, [sys.executable, "-c", "pass"], settle=0, **kw)
        return seen[0]

    def _record(self, pack, follow=None):
        """capture.around over a 1 s, 30-frame video; follow: what motion.keyboard does."""
        with tmpdir() as d:
            src = os.path.join(d, "src.mkv")
            run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=90x160:d=1:r=30",
                 "-vf", "drawbox=x=10:y=10:w=60:h=60:color=black:t=fill:enable='gt(n,15)'", "-c:v", "ffv1", src])
            rec = mock.Mock(started=time.time())

            def stop(r):
                shutil.copyfile(src, r.dst)
            with mock.patch.object(devices, "start_recording", lambda dev, dst, limit_s=60: setattr(rec, "dst", dst)
                                   or rec), \
                    mock.patch.object(devices, "stop_recording", stop), \
                    mock.patch.object(capture, "follow_keyboard", follow or (lambda p: None)), \
                    mock.patch.object(capture.time, "sleep", lambda s: None):
                capture.around("android", "e", pack, ["true"], settle=0)
            return read_json(os.path.join(pack, "around.json"))

    def test_a_recheck_recording_keeps_every_frame(self):
        # 0.5.12 trip-map: the verifier asked for every frame of 3 s and got 3 + 8 + 4 in sheets
        with tmpdir() as d:
            recheck = os.path.join(d, "android", "trip-map", "default-tap-outside", "drag-1-recheck")
            self.assertEqual(self._record(recheck)["frames_all"], 30)
            self.assertEqual(len(os.listdir(os.path.join(recheck, "frames_all"))), 30)
            first = os.path.join(d, "android", "trip-map", "default-tap-outside", "drag-1")
            self.assertNotIn("frames_all", self._record(first))

    def test_keyboard_json_comes_with_the_snap_and_again_after_kb_hide(self):
        # 0.5.12: keyboard-open rechecks ran the focus and Back without motion.py --keyboard: no keyboard.json
        seen = []
        with tmpdir() as d:
            pack = os.path.join(d, "android", "thread", "default-keyboard-open-recheck-1")
            os.makedirs(os.path.join(pack, "kb-show"))
            open(os.path.join(pack, "kb-show", "video.mp4"), "w").close()
            with open(os.path.join(FIXTURES, "settings_uiautomator.xml"), encoding="utf-8") as f:
                xml = f.read()
            with mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml), \
                    mock.patch.object(devices, "screenshot", fake_screenshot), \
                    mock.patch.object(devices, "px_per_dp", lambda dv: 2.625), \
                    mock.patch.object(devices, "keyboard_top", lambda dv, n=(): 1500), \
                    mock.patch.object(capture, "follow_keyboard", seen.append):
                capture.snap("android", "e", pack, keyboard=True)
            self.assertEqual(read_json(os.path.join(pack, "meta.json"))["keyboard_json"], "written")

            def no_field(p):
                raise capture.FEError("no focused field in %s" % p)
            self.assertEqual(self._record(os.path.join(pack, "kb-hide"), follow=seen.append)["keyboard_json"],
                             "written")
            self.assertIn("no focused field", self._record(os.path.join(pack, "kb-hide"), no_field)["keyboard_json"])
        self.assertEqual(seen, [pack, pack])

    def test_a_failing_call_still_stops_the_recorder(self):
        stopped = []

        def boom():
            raise capture.FEError("adb gone")
        with tmpdir() as d, mock.patch.object(devices, "start_recording", lambda dev, dst, limit_s=60: "rec"), \
                mock.patch.object(devices, "stop_recording", stopped.append), \
                mock.patch.object(capture.time, "sleep", lambda s: None):
            with self.assertRaises(capture.FEError):
                capture.around("android", "e", os.path.join(d, "p"), ["taps.py", "slow"], call=boom)
        self.assertEqual(stopped, ["rec"])

    def test_ceiling_comes_from_the_runs_coverage(self):
        with tmpdir() as d:
            write_json(os.path.join(d, "coverage.json"), {"cells": [], "motion_ceiling_ms": 300})
            pack = os.path.join(d, "android", "feed", "default-tap-back")
            self.assertEqual(self._ceiling(pack), 300)
            self.assertEqual(self._ceiling(pack, ceiling_ms=700), 700)  # the flag overrides

    def test_ceiling_without_a_run_is_the_default(self):
        with tmpdir() as d:
            self.assertEqual(self._ceiling(os.path.join(d, "p")), DEFAULT_CEILING_MS)



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


def _node(i, cls, bounds, depth=1, text=""):
    return {"id": "n%d" % i, "parent": None, "depth": depth, "cls": cls, "text": text, "label": "", "rid": "",
            "bounds": bounds, "clickable": False, "focused": False, "enabled": True}


class FakeList(object):
    """A 360x800 screen: a sticky header over rows every 200 px of a `content` px list; a drag loses 8 px of slop
    (px_per_dp 1). `spinner`: a square that flips black and white on every frame, an animation that never stops.
    `fail`: {page: exit} for snaps that fail."""
    W, H, SLOP = 360, 800, 8

    def __init__(self, content, spinner=False, fail=None):
        self.content, self.spinner, self.fail = content, spinner, fail or {}
        self.off = self.pulled = self.frames = 0  # pulled: px dragged past the top (pull-to-refresh)

    def visible(self):
        return [(k, y - self.off) for k, y in enumerate(range(100, self.content, 200))
                if 0 < y - self.off < self.H - 100]

    def frame(self, dst):
        self.frames += 1
        rects = [(0, 0, self.W, 60, (200, 200, 200), 0)] + [(20, y, 320, 100, (40, 40, 40), 8)
                                                            for _, y in self.visible()]
        if self.spinner:
            rects.append((280, 680, 60, 60, (0, 0, 0) if self.frames % 2 else (255, 255, 255), 0))
        return make_png(dst, self.W, self.H, rects=rects)

    def snap(self, platform, device_id, out, *a, **kw):
        os.makedirs(out, exist_ok=True)
        self.frame(os.path.join(out, "screen.png"))
        nodes = [_node(0, "android.widget.ScrollView", [0, 0, self.W, self.H], 1),
                 _node(1, "android.widget.TextView", [0, 10, 100, 40], 2, "Back"),
                 _node(2, "android.widget.TextView", [100, 10, 160, 40], 2, "Header"),
                 _node(3, "android.widget.TextView", [260, 10, 100, 40], 2, "Share")]
        nodes += [_node(4 + k, "android.widget.TextView", [20, y, 320, 100], 2, "row %d" % k)
                  for k, y in self.visible()]
        write_json(os.path.join(out, "nodes.json"), nodes)
        write_json(os.path.join(out, "meta.json"), {"screen_wh": [self.W, self.H], "px_per_dp": 1.0})
        return self.fail.get(os.path.basename(out), 0)

    def swipe(self, dev, x1, y1, x2, y2, ms=300):
        d = y1 - y2
        step = (abs(d) - self.SLOP) * (1 if d > 0 else -1)
        self.pulled = max(self.pulled, -(self.off + step))
        self.off = max(0, min(self.content - self.H, self.off + step))

    @contextlib.contextmanager
    def pack(self):
        with tmpdir() as d, mock.patch.object(capture, "snap", self.snap), \
                mock.patch.object(devices, "swipe", self.swipe), \
                mock.patch.object(devices, "keyboard_top", lambda dev, n=(): None), \
                mock.patch.object(devices, "screenshot", lambda dev, dst: self.frame(dst)), \
                mock.patch.object(capture.time, "sleep", lambda s: None):
            pack = os.path.join(d, "default-none")
            self.snap("android", "emulator-5560", pack)
            yield pack


def _scrolls(pack):
    return sorted(n for n in os.listdir(pack) if n.startswith("scroll-"))


class PagesTest(unittest.TestCase):
    def test_main_list_is_the_innermost_tall_vertical_scroll(self):
        nodes = [_node(0, "android.widget.ScrollView", [0, 0, 1080, 2400], 0),
                 _node(1, "android.widget.ScrollView", [0, 63, 1080, 2085], 2),
                 _node(2, "android.widget.HorizontalScrollView", [0, 0, 1080, 2400], 4),
                 _node(3, "android.widget.ScrollView", [0, 300, 1080, 300], 5)]
        self.assertEqual(capture.main_list(nodes, 2400)["id"], "n1")
        self.assertIsNone(capture.main_list(nodes[3:], 2400))

    def test_scrolls_page_by_page_to_the_end_and_back_to_the_top(self):
        fake = FakeList(1800)
        with fake.pack() as pack:
            meta = capture.pages("android", "emulator-5560", pack)
            self.assertEqual((meta["scroll_pages"], meta["scroll_stop"], meta["scroll_restored"]), (3, "end", True))
            self.assertEqual((fake.off, fake.pulled), (0, 0))  # the same distances back: no pull past the top
            self.assertEqual(_scrolls(pack), ["scroll-1", "scroll-2", "scroll-3"])
            self.assertEqual(read_json(os.path.join(pack, "meta.json"))["scroll_pages"], 3)
            self.assertFalse(os.path.exists(os.path.join(pack, ".top.png")))

    def test_a_long_screen_stops_at_the_page_limit(self):
        with FakeList(1800).pack() as pack:
            meta = capture.pages("android", "emulator-5560", pack, max_pages=2)
        self.assertEqual((meta["scroll_pages"], meta["scroll_stop"]), (2, "limit"))

    def test_rows_that_stay_put_end_the_list_while_an_animation_runs(self):
        fake = FakeList(1000, spinner=True)  # the first drag already reaches the end
        with fake.pack() as pack:
            meta = capture.pages("android", "emulator-5560", pack)
            self.assertEqual((meta["scroll_pages"], meta["scroll_stop"], meta["scroll_restored"]), (1, "end", True))
            self.assertEqual(_scrolls(pack), ["scroll-1"])
        self.assertEqual((fake.off, fake.pulled), (0, 0))  # back by what moved, not by a full step

    def test_a_failed_page_snap_is_reported(self):
        with FakeList(1800, fail={"scroll-2": 3}).pack() as pack:
            meta = capture.pages("android", "emulator-5560", pack)
            self.assertEqual(_scrolls(pack), ["scroll-1"])
        self.assertEqual((meta["scroll_pages"], meta["scroll_stop"]), (1, "snap failed on page 2 (exit 3)"))

    def test_page_drags_stay_above_an_open_keyboard(self):
        box = [0, 200, 1080, 2000]
        self.assertEqual(capture._band(box), (540, 1700, 700))
        cx, low, high = capture._band(box, kb_top=1400)
        self.assertLess(low, 1400)
        self.assertGreater(high, 200)
        self.assertIsNone(capture._band(box, kb_top=600))  # under a quarter of the list is left

    def test_shift_tells_no_common_text_from_texts_that_stayed(self):
        box = [0, 0, 360, 800]
        a = [_node(1, "T", [0, 300, 100, 40], text="row 1"), _node(2, "T", [0, 500, 100, 40], text="row 2")]
        moved = [_node(2, "T", [0, 100, 100, 40], text="row 2")]
        other = [_node(3, "T", [0, 300, 100, 40], text="row 3")]
        self.assertEqual(capture._shift(a, moved, box), 400)
        self.assertIsNone(capture._shift(a, other, box))
        self.assertEqual(capture._shift(a, a, box), 0)

    def test_a_screen_without_a_list_gets_no_pages(self):
        with tmpdir() as d, mock.patch.object(devices, "screenshot", fake_screenshot):
            pack = os.path.join(d, "p")
            os.makedirs(pack)
            shutil.copyfile(os.path.join(FIXTURES, "settings.png"), os.path.join(pack, "screen.png"))
            write_json(os.path.join(pack, "nodes.json"), [_node(0, "android.widget.FrameLayout", [0, 0, 1080, 2400])])
            write_json(os.path.join(pack, "meta.json"), {"screen_wh": [1080, 2400]})
            meta = capture.pages("android", "emulator-5560", pack)
        self.assertEqual((meta["scroll_pages"], meta["scroll_restored"]), (0, True))


class CliTest(unittest.TestCase):
    def test_snap_passes_the_package_and_prints_where_the_pages_stopped(self):
        seen = {}

        def snap(platform, device_id, out, **kw):
            seen.update(kw)
            write_json(os.path.join(out, "meta.json"), {"app_running": True})
            return 0
        pages = {"scroll_pages": 1, "scroll_stop": "snap failed on page 2 (exit 3)", "scroll_restored": False}
        with tmpdir() as d, mock.patch.object(capture, "snap", snap), \
                mock.patch.object(capture, "pages", lambda *a: pages), \
                mock.patch.object(sys, "argv", ["capture.py", "snap", "--platform", "android", "--device", "e",
                                                "--out", d, "--package", "com.example.app", "--pages"]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(capture.main(), 0)
        self.assertEqual(seen["package"], "com.example.app")
        self.assertEqual(json.loads(out.getvalue())["scroll_stop"], "snap failed on page 2 (exit 3)")


if __name__ == "__main__":
    unittest.main()
