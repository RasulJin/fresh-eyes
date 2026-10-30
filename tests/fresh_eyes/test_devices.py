import json
import subprocess
import unittest
from unittest import mock

import tests.fresh_eyes.helpers  # noqa: F401
import devices
import fe_common
from devices import Device
from fe_common import FEError


class FakeRun:
    """Answers commands by substring; records every call."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, cmd, timeout=60, input=None, check=True):
        self.calls.append(cmd)
        joined = " ".join(cmd)
        out = b""
        for key, val in self.answers.items():
            if key in joined:
                out = val if isinstance(val, bytes) else val.encode()
                break
        return subprocess.CompletedProcess(cmd, 0, out, b"")


SIMCTL_JSON = json.dumps({"devices": {
    "com.apple.CoreSimulator.SimRuntime.iOS-26-2": [
        {"udid": "AAA", "name": "fresh-eyes iPhone 17", "state": "Booted"},
        {"udid": "BBB", "name": "iPhone 17 Pro", "state": "Shutdown"}]}})


class DevicesTest(unittest.TestCase):
    def patch(self, answers):
        answers = dict({"emu avd name": "fresh-eyes-pixel\nOK\n"}, **answers)
        fake = FakeRun(answers)
        p = mock.patch.object(fe_common, "run", fake)
        p.start()
        self.addCleanup(p.stop)
        return fake

    def test_is_owned(self):
        self.assertTrue(devices.is_owned(Device("android", "emulator-5560", "fresh-eyes-pixel")))
        self.assertFalse(devices.is_owned(Device("android", "emulator-5554", "")))
        self.assertFalse(devices.is_owned(Device("ios", "X", "iPhone 17")))

    def test_list_devices_parses_adb_and_simctl(self):
        self.patch({"booted -j": SIMCTL_JSON, "emu avd name": "fresh-eyes-pixel\nOK\n",
                    " devices": "List of devices attached\nemulator-5560\tdevice\nemulator-5554\toffline\n\n"})
        found = devices.list_devices()
        self.assertIn(Device("android", "emulator-5560", "fresh-eyes-pixel"), found)
        self.assertIn(Device("ios", "AAA", "fresh-eyes iPhone 17"), found)
        self.assertEqual(len(found), 2)

    def test_clear_app_refuses_foreign_device(self):
        fake = self.patch({"emu avd name": "Pixel_8\nOK\n"})
        with self.assertRaises(FEError):
            devices.clear_app(Device("android", "emulator-5554", "Pixel_8"), "com.example.app")
        self.assertFalse(any("pm clear" in " ".join(c) for c in fake.calls))

    def test_owned_label_on_a_foreign_emulator_is_refused(self):
        fake = self.patch({"emu avd name": "My_QA_Device\nOK\n"})
        for fn in (lambda d: devices.set_dark(d, True), lambda d: devices.tap(d, 10, 10),
                   lambda d: devices.clear_app(d, "com.example.app")):
            with self.assertRaises(FEError) as e:
                fn(Device("android", "emulator-5560", "fresh-eyes-pixel"))
            self.assertIn("My_QA_Device", str(e.exception))
        self.assertFalse(any("input" in " ".join(c) or "uimode" in " ".join(c) for c in fake.calls))

    def test_simulator_name_read_from_simctl_not_label(self):
        self.patch({"list devices -j": SIMCTL_JSON})
        self.assertEqual(devices.real_name(Device("ios", "BBB", "fresh-eyes iPhone 17")), "iPhone 17 Pro")

    def test_a_phone_is_never_owned(self):
        self.patch({})
        with self.assertRaises(FEError):
            devices.tap(Device("android", "R58M123", "fresh-eyes-pixel"), 10, 10)

    def test_invalid_app_id_refused(self):
        self.patch({})
        with self.assertRaises(FEError):
            devices.launch(Device("android", "emulator-5560", "fresh-eyes-pixel"), "com.x; reboot")

    def test_settings_refuse_foreign_device(self):
        self.patch({})
        foreign = Device("ios", "BBB", "iPhone 17 Pro")
        for fn in (lambda: devices.set_dark(foreign, True), lambda: devices.set_font_scale(foreign, "xl"),
                   lambda: devices.set_network(foreign, False)):
            with self.assertRaises(FEError):
                fn()

    def test_rapid_taps_backgrounds_in_one_shell(self):
        fake = self.patch({})
        devices.rapid_taps(Device("android", "emulator-5560", "fresh-eyes-pixel"), 10, 20, 3, 30)
        taps = [c for c in fake.calls if "input tap" in c[-1]]
        self.assertEqual(len(taps), 1)
        shell = taps[0][-1]
        self.assertEqual(shell.count("input tap 10 20 &"), 3)
        self.assertIn("sleep 0.03", shell)
        self.assertTrue(shell.endswith("wait"))

    def test_tap_then_back_in_one_shell(self):
        fake = self.patch({})
        devices.tap_then_back(Device("android", "emulator-5560", "fresh-eyes-pixel"), 10, 20, 100)
        shells = [c[-1] for c in fake.calls if "input" in c[-1]]
        self.assertEqual(shells, ["input tap 10 20; sleep 0.1; input keyevent 4"])

    def test_drag_back_presses_back_while_the_drag_runs(self):
        fake = self.patch({})
        devices.drag_then_back(Device("android", "emulator-5560", "fresh-eyes-pixel"), 700, 1200, 250, 1200, 2000, 700)
        self.assertEqual([c[-1] for c in fake.calls if "input" in c[-1]],
                         ["input swipe 700 1200 250 1200 2000 & sleep 0.7; input keyevent 4; wait"])

    def test_swipe_tap_taps_the_moment_the_drag_ends(self):
        fake = self.patch({})
        devices.swipe_then_tap(Device("android", "emulator-5560", "fresh-eyes-pixel"), 700, 1200, 100, 1200, 540, 305)
        self.assertEqual([c[-1] for c in fake.calls if "input" in c[-1]],
                         ["input swipe 700 1200 100 1200 250; sleep 0.3; input tap 540 305"])

    def test_drag_races_are_android_only_and_owned_only(self):
        for answers, dev in (({"list devices -j": SIMCTL_JSON}, Device("ios", "AAA", "fresh-eyes iPhone 17")),
                             ({"emu avd name": "Pixel_9\nOK\n"}, Device("android", "emulator-5554", "Pixel_9"))):
            fake = self.patch(answers)
            with self.assertRaisesRegex(FEError, "Android only" if dev.platform == "ios" else "Pixel_9"):
                devices.drag_then_back(dev, 1, 2, 3, 4)
            with self.assertRaises(FEError):
                devices.swipe_then_tap(dev, 1, 2, 3, 4, 5, 6)
            self.assertFalse(any("input" in " ".join(c) for c in fake.calls))

    def test_rapid_taps_zero_interval_has_no_sleep(self):
        fake = self.patch({})
        devices.rapid_taps(Device("android", "emulator-5560", "x"), 1, 2, 2, 0)
        self.assertNotIn("sleep", fake.calls[0][-1])

    def test_px_per_dp_prefers_override(self):
        self.patch({"wm density": "Physical density: 420\nOverride density: 480\n"})
        self.assertEqual(devices.px_per_dp(Device("android", "emulator-5560", "x")), 3.0)

    def test_keyboard_hidden_is_none(self):
        self.patch({"input_method": "mInputShown=false\n"})
        self.assertIsNone(devices.keyboard_top(Device("android", "emulator-5560", "x")))

    def test_keyboard_top_from_window_frame(self):
        self.patch({"input_method": "mInputShown=true\n",
                    "window InputMethod": "Window{abc InputMethod}:\n    mFrame=[0,1484][1080,2400] ...\n"})
        self.assertEqual(devices.keyboard_top(Device("android", "emulator-5560", "x")), 1484)

    def test_apply_setting_maps_names(self):
        fake = self.patch({})
        own = Device("android", "emulator-5560", "fresh-eyes-pixel")
        devices.apply_setting(own, "dark")
        devices.apply_setting(own, "font-xl")
        devices.apply_setting(own, "offline")
        shells = " | ".join(c[-1] for c in fake.calls)
        self.assertIn("cmd uimode night yes", shells)
        self.assertIn("font_scale 1.3", shells)
        self.assertIn("svc wifi disable", shells)

    def test_apply_setting_unknown_and_foreign(self):
        self.patch({})
        with self.assertRaises(FEError):
            devices.apply_setting(Device("android", "emulator-5560", "fresh-eyes-pixel"), "sepia")
        self.patch({"emu avd name": "Pixel_8\nOK\n"})
        with self.assertRaises(FEError):
            devices.apply_setting(Device("android", "emulator-5554", ""), "dark")

    def test_keyboard_top_android16_touchable_region(self):
        self.patch({"input_method": "mInputShown=true\n",
                    "window InputMethod": "    mGivenContentInsets=[0,1454][0,0]\n    touchable region=SkRegion((0,1517,1080,2400))\n"})
        self.assertEqual(devices.keyboard_top(Device("android", "emulator-5560", "x")), 1517)

    def test_launch_resolves_launcher_activity(self):
        fake = self.patch({"resolve-activity": "priority=0 preferredOrder=0\ncom.example.app/.MainActivity\n"})
        devices.launch(Device("android", "emulator-5560", "fresh-eyes-pixel"), "com.example.app")
        self.assertIn("am start -n com.example.app/.MainActivity", " | ".join(c[-1] for c in fake.calls))

    def test_disable_stylus_handwriting(self):
        fake = self.patch({})
        devices.disable_stylus_handwriting(Device("android", "emulator-5560", "fresh-eyes-pixel"))
        shells = " | ".join(c[-1] for c in fake.calls)
        self.assertIn("settings put secure stylus_handwriting_enabled 0", shells)
        self.assertIn("am force-stop com.google.android.inputmethod.latin", shells)

    def test_stylus_handwriting_on_when_unset(self):
        self.patch({"stylus_handwriting_enabled": "null\n"})
        self.assertTrue(devices.stylus_handwriting_on(Device("android", "emulator-5560", "x")))

    def test_app_build_is_the_apk_sha256(self):
        sha = "a" * 64
        self.patch({"pm path": "package:/data/app/x/split_config.xxhdpi.apk\npackage:/data/app/x/base.apk\n",
                    "sha256sum /data/app/x/base.apk": sha + "  /data/app/x/base.apk\n"})
        self.assertEqual(devices.app_build(Device("android", "emulator-5560", "x"), "com.example.app"), sha)
        self.patch({"pm path": ""})
        self.assertIsNone(devices.app_build(Device("android", "emulator-5560", "x"), "com.example.app"))
        self.patch({"pm path": "package:/data/app/x/base.apk\n",
                    "sha256sum": "sha256sum: /data/app/x/base.apk: Permission denied\n"})
        self.assertIsNone(devices.app_build(Device("android", "emulator-5560", "x"), "com.example.app"))

    def test_ios_app_build_hashes_the_executable(self):
        import hashlib
        import os
        import plistlib
        from tests.fresh_eyes.helpers import tmpdir
        with tmpdir() as d:
            with open(os.path.join(d, "Info.plist"), "wb") as f:
                plistlib.dump({"CFBundleExecutable": "App"}, f)
            with open(os.path.join(d, "App"), "wb") as f:
                f.write(b"binary")
            self.patch({"get_app_container": d + "\n"})
            self.assertEqual(devices.app_build(Device("ios", "AAA", "x"), "com.example.app"),
                             hashlib.sha256(b"binary").hexdigest())
        self.patch({"get_app_container": ""})
        self.assertIsNone(devices.app_build(Device("ios", "AAA", "x"), "com.example.app"))

    def test_animations_disabled_detected(self):
        self.patch({"window_animation_scale": "0.0\n", "transition_animation_scale": "1.0\n",
                    "animator_duration_scale": "1.0\n"})
        self.assertFalse(devices.animations_enabled(Device("android", "emulator-5560", "x")))



class IosDriverTest(unittest.TestCase):
    DEV = Device("ios", "1EB9BE1B", "fresh-eyes iPhone 17")
    INFO = json.dumps({"widthPoints": 402, "heightPoints": 874, "widthPixels": 1206, "heightPixels": 2622})

    def setUp(self):
        p = mock.patch.object(devices, "real_name", lambda dev: "fresh-eyes iPhone 17")
        p.start()
        self.addCleanup(p.stop)

    def fake_call(self):
        sent = []

        def call(dev, route, body=None, timeout=20, base=None):
            sent.append((route, body))
            return self.INFO if route == "deviceInfo" else "{}"
        return sent, call

    def test_driver_url_found_by_udid_in_process_command(self):
        fake = FakeRun({"lsof": "p111\nn127.0.0.1:22087\np222\nn127.0.0.1:22088\n",
                        "ps -p 111": "/Devices/OTHER-UDID/maestro-driver-iosUITests-Runner",
                        "ps -p 222": "/Devices/1EB9BE1B/data/maestro-driver-iosUITests-Runner"})
        with mock.patch.object(fe_common, "run", fake):
            self.assertEqual(devices.ios_driver(self.DEV), "http://127.0.0.1:22088")

    def test_no_driver_says_how_to_start_it(self):
        with mock.patch.object(fe_common, "run", FakeRun({})):
            with self.assertRaises(FEError) as e:
                devices.ios_driver(self.DEV)
        self.assertIn("inspect_screen", str(e.exception))

    def test_tap_converts_pixels_to_points(self):
        sent, call = self.fake_call()
        with mock.patch.object(devices, "ios_call", call):
            devices.tap(self.DEV, 603, 1065)
        self.assertEqual(sent[-1], ("touch", {"x": 201.0, "y": 355.0, "duration": 0}))

    def test_rapid_taps_send_count_touches(self):
        sent, call = self.fake_call()
        with mock.patch.object(devices, "ios_call", call), \
                mock.patch.object(devices, "ios_driver", lambda dev: "http://x"):
            devices.rapid_taps(self.DEV, 603, 1065, 3, 0)
        self.assertEqual([r for r, _ in sent].count("touch"), 3)

    def test_landscape_goes_through_the_driver(self):
        sent, call = self.fake_call()
        with mock.patch.object(devices, "ios_call", call):
            devices.apply_setting(self.DEV, "landscape")
            devices.apply_setting(self.DEV, "portrait")
        self.assertEqual(sent, [("setOrientation", {"orientation": "landscapeLeft"}),
                                ("setOrientation", {"orientation": "portrait"})])

    def test_back_is_edge_swipe(self):
        sent, call = self.fake_call()
        with mock.patch.object(devices, "ios_call", call), \
                mock.patch.object(devices, "ios_driver", lambda dev: "http://x"):
            devices.back(self.DEV)
        route, body = sent[-1]
        self.assertEqual(route, "swipeV2")
        self.assertLess(body["startX"], 5)
        self.assertGreater(body["endX"], 300)


class AvdTest(unittest.TestCase):
    def test_small_avd_gets_small_data_partition(self):
        from tests.fresh_eyes.helpers import tmpdir
        import os
        image = ("system-images/android-36/google_apis/x86_64/", "android-36", "x86_64", "google_apis")
        with tmpdir() as d, mock.patch.object(devices, "AVD_DIR", d), \
                mock.patch.object(devices, "system_image", lambda: image):
            devices._write_avd("fresh-eyes-small", 720, 1280, 320, devices.ANDROID_DATA_BYTES["small"])
            with open(os.path.join(d, "fresh-eyes-small.avd", "config.ini")) as f:
                cfg = f.read()
        self.assertIn("abi.type=x86_64", cfg)
        self.assertIn("image.sysdir.1=system-images/android-36/google_apis/x86_64/", cfg)
        self.assertIn("disk.dataPartition.size=2147483648", cfg)
        self.assertIn("hw.keyboard=no", cfg)

    def test_system_image_newest_api_for_this_cpu(self):
        from tests.fresh_eyes.helpers import tmpdir
        import os
        with tmpdir() as d, mock.patch.object(devices, "sdk_root", lambda: d), \
                mock.patch.object(devices.platform, "machine", lambda: "arm64"):
            for rel in ("android-35/google_apis_playstore/arm64-v8a", "android-36.1/google_apis/arm64-v8a",
                        "android-36.1/google_apis_playstore/x86_64"):
                os.makedirs(os.path.join(d, "system-images", rel))
            self.assertEqual(devices.system_image(), ("system-images/android-36.1/google_apis/arm64-v8a/",
                                                      "android-36.1", "arm64-v8a", "google_apis"))
        with tmpdir() as d, mock.patch.object(devices, "sdk_root", lambda: d):
            with self.assertRaises(FEError):
                devices.system_image()

    def test_stop_owned_shuts_down_only_our_devices(self):
        fake = FakeRun({"emulator-5554 emu avd name": "Pixel_8\nOK\n",
                        "emulator-5560 emu avd name": "fresh-eyes-pixel\nOK\n", "booted -j": SIMCTL_JSON,
                        " devices": "List of devices attached\nemulator-5560\tdevice\nemulator-5554\tdevice\n"})
        with mock.patch.object(fe_common, "run", fake):
            self.assertEqual(devices.stop_owned("android"), ["fresh-eyes-pixel"])
            self.assertEqual(devices.stop_owned("ios"), ["fresh-eyes iPhone 17"])
        joined = [" ".join(c) for c in fake.calls]
        self.assertTrue(any("emulator-5560 emu kill" in c for c in joined))
        self.assertFalse(any("emulator-5554 emu kill" in c for c in joined))
        self.assertTrue(any(c.endswith("shutdown AAA") for c in joined))

    def test_emulator_never_saves_a_boot_snapshot(self):
        from tests.fresh_eyes.helpers import tmpdir
        import os
        seen = []

        class DeadProc(object):
            def poll(self):
                return 1

        def fake_popen(args, **k):
            seen.extend(args)
            return DeadProc()

        with tmpdir() as d:
            open(os.path.join(d, "fresh-eyes-small.ini"), "w").close()
            with mock.patch.object(devices, "AVD_DIR", d), mock.patch.object(devices, "list_devices", lambda: []), \
                    mock.patch.object(devices.subprocess, "Popen", fake_popen), \
                    mock.patch.object(devices, "_open_log", lambda name: None), \
                    mock.patch.object(devices, "_log_tail", lambda name: ""):
                with self.assertRaises(FEError):
                    devices.ensure_owned("android", "small", boot_timeout=30)
        self.assertIn("-no-snapshot-save", seen)

    def test_ensure_owned_fails_fast_when_emulator_exits(self):
        from tests.fresh_eyes.helpers import tmpdir
        import os

        class DeadProc(object):
            def poll(self):
                return 1

        with tmpdir() as d:
            open(os.path.join(d, "fresh-eyes-small.ini"), "w").close()
            with open(os.path.join(d, "fresh-eyes-small.fresh-eyes.log"), "w") as f:
                f.write("INFO ok\nFATAL | Not enough space to create userdata partition.\n")

            def fake_popen(*a, **k):
                return DeadProc()

            with mock.patch.object(devices, "AVD_DIR", d), \
                    mock.patch.object(devices, "list_devices", lambda: []), \
                    mock.patch.object(devices.subprocess, "Popen", fake_popen), \
                    mock.patch.object(devices, "_open_log", lambda name: None), \
                    mock.patch.object(devices.time, "sleep", lambda s: None):
                with self.assertRaises(FEError) as e:
                    devices.ensure_owned("android", "small", boot_timeout=30)
        self.assertIn("Not enough space", str(e.exception))


if __name__ == "__main__":
    unittest.main()
