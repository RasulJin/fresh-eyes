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
        fake = self.patch({})
        with self.assertRaises(FEError):
            devices.clear_app(Device("android", "emulator-5554", "Pixel_8"), "com.voiova.app")
        self.assertEqual(fake.calls, [])

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
        self.assertEqual(len(fake.calls), 1)
        shell = fake.calls[0][-1]
        self.assertEqual(shell.count("input tap 10 20 &"), 3)
        self.assertIn("sleep 0.03", shell)
        self.assertTrue(shell.endswith("wait"))

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
        with self.assertRaises(FEError):
            devices.apply_setting(Device("android", "emulator-5554", ""), "dark")

    def test_keyboard_top_android16_touchable_region(self):
        self.patch({"input_method": "mInputShown=true\n",
                    "window InputMethod": "    mGivenContentInsets=[0,1454][0,0]\n    touchable region=SkRegion((0,1517,1080,2400))\n"})
        self.assertEqual(devices.keyboard_top(Device("android", "emulator-5560", "x")), 1517)

    def test_launch_resolves_launcher_activity(self):
        fake = self.patch({"resolve-activity": "priority=0 preferredOrder=0\ncom.voiova.app/.MainActivity\n"})
        devices.launch(Device("android", "emulator-5560", "fresh-eyes-pixel"), "com.voiova.app")
        self.assertIn("am start -n com.voiova.app/.MainActivity", " | ".join(c[-1] for c in fake.calls))

    def test_disable_stylus_handwriting(self):
        fake = self.patch({})
        devices.disable_stylus_handwriting(Device("android", "emulator-5560", "fresh-eyes-pixel"))
        shells = " | ".join(c[-1] for c in fake.calls)
        self.assertIn("settings put secure stylus_handwriting_enabled 0", shells)
        self.assertIn("am force-stop com.google.android.inputmethod.latin", shells)

    def test_stylus_handwriting_on_when_unset(self):
        self.patch({"stylus_handwriting_enabled": "null\n"})
        self.assertTrue(devices.stylus_handwriting_on(Device("android", "emulator-5560", "x")))

    def test_animations_disabled_detected(self):
        self.patch({"window_animation_scale": "0.0\n", "transition_animation_scale": "1.0\n",
                    "animator_duration_scale": "1.0\n"})
        self.assertFalse(devices.animations_enabled(Device("android", "emulator-5560", "x")))


if __name__ == "__main__":
    unittest.main()


class AvdTest(unittest.TestCase):
    def test_small_avd_gets_small_data_partition(self):
        from tests.fresh_eyes.helpers import tmpdir
        import os
        with tmpdir() as d, mock.patch.object(devices, "AVD_DIR", d):
            devices._write_avd("fresh-eyes-small", 720, 1280, 320, devices.ANDROID_DATA_BYTES["small"])
            cfg = open(os.path.join(d, "fresh-eyes-small.avd", "config.ini")).read()
        self.assertIn("disk.dataPartition.size=2147483648", cfg)
        self.assertIn("hw.keyboard=no", cfg)

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
