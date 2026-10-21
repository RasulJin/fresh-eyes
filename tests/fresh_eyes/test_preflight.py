import contextlib
import io
import os
import unittest
from unittest import mock

from tests.fresh_eyes.helpers import tmpdir
import devices
import preflight
from devices import Device
from fe_common import write_json

PIXEL = Device("android", "emulator-5560", "fresh-eyes-pixel")
STAGING = "https://api-staging.example.com"
PROD = "https://api.example.com"


def config(url=STAGING, name="staging"):
    return {"platforms": ["android"], "language": "ru",
            "app": {"android_id": "com.example.app", "ios_id": "com.example.app", "expo_root": "apps/mobile"},
            "env": {"name": name, "api_expected": STAGING, "api_forbidden": [PROD]},
            "builds": {"quick": {"android": {"metro_env": {"EXPO_PUBLIC_API_URL": url}}},
                       "full": {"android": {"artifact": "build/app.apk"}}},
            "screens": []}


class PreflightTest(unittest.TestCase):
    def fake_devices(self, soft_keyboard=(True,), installed=True, listed=(PIXEL,)):
        state = {"kb": list(soft_keyboard)}
        calls = []

        def kb(_dev):
            return state["kb"][0] if len(state["kb"]) == 1 else state["kb"].pop(0)

        patches = {
            "list_devices": lambda: list(listed),
            "ensure_owned": lambda platform, kind="default": None,
            "app_installed": lambda dev, app: installed,
            "soft_keyboard_on": kb,
            "set_soft_keyboard": lambda dev: calls.append("set_soft_keyboard"),
            "animations_enabled": lambda dev: True,
            "enable_animations": lambda dev: calls.append("enable_animations"),
            "stylus_handwriting_on": lambda dev: state.get("stylus", False),
            "disable_stylus_handwriting": lambda dev: (calls.append("disable_stylus"), state.update(stylus=False)),
        }
        for name, fn in patches.items():
            p = mock.patch.object(devices, name, fn)
            p.start()
            self.addCleanup(p.stop)
        self.bundle(metro="x " + STAGING + " y")
        return calls

    def bundle(self, installed=None, metro=None):
        """What the app on the device runs: a release bundle inside the app, or the one Metro serves."""
        def from_metro(platform, port):
            if metro is None:
                raise preflight.FEError("no Metro bundle on port %d" % port)
            return metro
        for name, fn in {"_installed_bundle": lambda p, dev, app, cache: installed,
                         "_metro_port": lambda p, dev: 8081, "_metro_bundle": from_metro}.items():
            patch = mock.patch.object(preflight, name, fn)
            patch.start()
            self.addCleanup(patch.stop)

    def test_soft_keyboard_off_is_reported(self):
        self.fake_devices(soft_keyboard=(False,))
        res = preflight.check(config(), "quick", fix=False)
        self.assertFalse(res["platforms"]["android"]["soft_keyboard"])
        self.assertFalse(res["platforms"]["android"]["ok"])

    def test_soft_keyboard_off_is_fixed(self):
        calls = self.fake_devices(soft_keyboard=(False, True))
        res = preflight.check(config(), "quick", fix=True)
        self.assertIn("set_soft_keyboard", calls)
        self.assertTrue(res["platforms"]["android"]["soft_keyboard"])
        self.assertTrue(res["platforms"]["android"]["ok"])

    def test_stylus_mode_is_reported_and_fixed(self):
        calls = self.fake_devices()
        devices.stylus_handwriting_on  # patched in fake_devices
        with mock.patch.object(devices, "stylus_handwriting_on", lambda dev: "disable_stylus" not in calls):
            res = preflight.check(config(), "quick", fix=False)
            self.assertFalse(res["platforms"]["android"]["ok"])
            self.assertIn("stylus", " ".join(res["platforms"]["android"]["problems"]))
            res = preflight.check(config(), "quick", fix=True)
        self.assertIn("disable_stylus", calls)
        self.assertTrue(res["platforms"]["android"]["ok"])

    def test_foreign_device_is_not_used(self):
        self.fake_devices(listed=(Device("android", "emulator-5554", ""),))
        res = preflight.check(config(), "quick", fix=False)
        self.assertFalse(res["platforms"]["android"]["ok"])

    def test_config_alone_proves_nothing_metro_serving_prod_blocks_mutation(self):
        self.fake_devices()  # config says staging...
        self.bundle(metro="x " + PROD)  # ...but Metro started without the override serves .env's production URL
        res = preflight.check(config(), "quick", fix=False)
        self.assertFalse(res["env"]["verified"])
        self.assertFalse(res["mutating_allowed"])
        self.assertIn("Metro", " ".join(res["unchecked"]))

    def test_mutating_allowed_staging_verified(self):
        self.fake_devices()
        res = preflight.check(config(), "quick", fix=False)
        self.assertTrue(res["env"]["verified"])
        self.assertTrue(res["mutating_allowed"])
        self.assertEqual(res["env"]["api_host"], STAGING)

    def test_no_metro_no_release_bundle_is_unverified(self):
        self.fake_devices()
        self.bundle()
        self.assertFalse(preflight.check(config(), "quick", fix=False)["env"]["verified"])

    def test_prod_env_name_never_mutates(self):
        self.fake_devices()
        self.assertFalse(preflight.check(config(name="prod"), "quick", fix=False)["mutating_allowed"])

    def test_installed_release_bundle_is_what_counts(self):
        self.fake_devices()
        self.bundle(installed="x " + STAGING, metro="x " + PROD)
        self.assertTrue(preflight.check(config(), "quick", fix=False)["env"]["verified"])
        self.bundle(installed="x " + STAGING + " " + PROD)
        self.assertFalse(preflight.check(config(), "full", fix=False)["env"]["verified"])

    def test_full_mode_needs_a_release_build(self):
        self.fake_devices()
        res = preflight.check(config(), "full", fix=False)
        self.assertFalse(res["env"]["verified"])
        self.assertIn("release build", " ".join(res["unchecked"]))

    def test_bundle_check_matches_host_without_scheme(self):
        # Hermes keeps the scheme and host in separate string-table entries
        self.fake_devices()
        self.bundle(installed="\x00api-staging.example.com\x00")
        self.assertTrue(preflight.check(config(), "full", fix=False)["env"]["verified"])
        self.bundle(installed="api-staging.example.com api.example.com")
        self.assertFalse(preflight.check(config(), "full", fix=False)["env"]["verified"])

    def test_ios_needs_the_maestro_driver(self):
        sim = Device("ios", "1EB9BE1B", "fresh-eyes iPhone 17")
        self.fake_devices(listed=(sim,))
        cfg = dict(config(), platforms=["ios"])

        def no_driver(dev):
            raise devices.FEError("no Maestro iOS driver for fresh-eyes iPhone 17: call the Maestro MCP inspect_screen")
        with mock.patch.object(devices, "ios_driver", no_driver):
            ios = preflight.check(cfg, "quick", fix=False)["platforms"]["ios"]
        self.assertFalse(ios["ok"])
        self.assertIn("inspect_screen", " ".join(ios["problems"]))
        with mock.patch.object(devices, "ios_driver", lambda dev: "http://127.0.0.1:22087"):
            self.assertTrue(preflight.check(cfg, "quick", fix=False)["platforms"]["ios"]["ok"])

    def test_platform_filter_reports_others_unchecked(self):
        self.fake_devices()
        cfg = dict(config(), platforms=["android", "ios"])
        res = preflight.check(cfg, "quick", fix=False, only=["android"])
        self.assertTrue(res["platforms"]["android"]["ok"])
        self.assertFalse(res["platforms"]["ios"]["ok"])
        self.assertIn("not requested", " ".join(res["unchecked"]))

    def test_brief_exit_zero_on_problems(self):
        self.fake_devices(listed=())
        with tmpdir() as d:
            write_json(os.path.join(d, ".fresh-eyes", "config.json"), config())
            out = io.StringIO()
            argv = ["preflight.py", "--project", d, "--mode", "quick", "--brief", "--out",
                    os.path.join(d, "pf.json")]
            with mock.patch("sys.argv", argv), contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as e:
                    preflight.cli_main()
            self.assertEqual(e.exception.code, 0)
            self.assertLessEqual(len(out.getvalue().strip().splitlines()), 15)
            self.assertTrue(os.path.exists(os.path.join(d, "pf.json")))


if __name__ == "__main__":
    unittest.main()
