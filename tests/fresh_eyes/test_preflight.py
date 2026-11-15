import contextlib
import io
import os
import subprocess
import types
import unittest
from unittest import mock

from tests.fresh_eyes.helpers import tmpdir
import devices
import preflight
from devices import Device
from fe_common import FEError, write_json

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
            "maestro_driver_running": lambda dev: state.get("maestro", False),
            "stop_maestro_driver": lambda dev: (calls.append("stop_maestro"), state.update(maestro=False)),
            "terminate": lambda dev, app: calls.append("terminate"),
            "app_build": lambda dev, app: "b" * 64,
            "photo_count": lambda dev: state.get("photos", 3),
            "push_photos": lambda dev, paths: (calls.append(("push_photos", len(paths))), state.update(photos=3)),
            "_adb": self.no_real_adb,
        }
        for name, fn in patches.items():
            p = mock.patch.object(devices, name, fn)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(preflight, "_smoke", lambda dev, app: state.get("smoke"))
        p.start()
        self.addCleanup(p.stop)
        self.state = state
        self.bundle(metro="x " + STAGING + " y")
        self.disk(free_gb=100)
        return calls

    @staticmethod
    def no_real_adb(*args, **kwargs):  # a test reaching adb drives whatever emulator is running (5560 often is)
        raise AssertionError("real adb call in a test: %s" % (args,))

    def disk(self, free_gb):
        p = mock.patch.object(preflight.shutil, "disk_usage", lambda path: types.SimpleNamespace(free=free_gb * 2 ** 30))
        p.start()
        self.addCleanup(p.stop)

    def test_a_nearly_full_disk_stops_the_run(self):
        self.fake_devices()
        self.assertTrue(preflight.check(config(), "quick")["ok"])
        self.disk(free_gb=8.6)
        res = preflight.check(config(), "quick")
        self.assertFalse(res["ok"])
        self.assertTrue(res["platforms"]["android"]["ok"])
        self.assertEqual(res["disk_free_gb"], 8.6)
        self.assertTrue(any("8.6 GB free" in u for u in res["unchecked"]))

    def bundle(self, installed=None, metro=None):
        """What the app on the device runs: a release bundle inside the app, or what each running Metro serves
        (metro: one bundle text, or {port: text} for several packagers)."""
        served = metro if isinstance(metro, dict) else ({8081: metro} if metro is not None else {})
        for name, fn in {"_installed_bundle": lambda p, dev, app, cache: installed,
                         "_metro_ports": lambda p, dev: sorted(served),
                         "_metro_bundle": lambda p, port: served[port]}.items():
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

    def test_leftover_maestro_driver_is_reported_and_stopped(self):
        calls = self.fake_devices()
        with mock.patch.object(devices, "maestro_driver_running", lambda dev: "stop_maestro" not in calls):
            res = preflight.check(config(), "quick", fix=False)
            self.assertFalse(res["platforms"]["android"]["ok"])
            self.assertIn("UiAutomation", " ".join(res["platforms"]["android"]["problems"]))
            res = preflight.check(config(), "quick", fix=True)
        self.assertIn("stop_maestro", calls)
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

    def test_mutating_allowed_staging_verified_and_app_restarted(self):
        calls = self.fake_devices()
        res = preflight.check(config(), "quick", fix=True)
        self.assertTrue(res["env"]["verified"])
        self.assertTrue(res["mutating_allowed"])
        self.assertEqual(res["env"]["api_host"], STAGING)
        self.assertIn("terminate", calls)  # so it reloads the verified bundle, not JS from an earlier Metro

    def test_metro_path_without_fix_is_not_verified(self):
        self.fake_devices()
        self.assertFalse(preflight.check(config(), "quick", fix=False)["env"]["verified"])

    def test_every_running_metro_must_serve_staging(self):
        self.fake_devices()
        self.bundle(metro={8081: "x " + PROD, 8082: "x " + STAGING})
        res = preflight.check(config(), "quick", fix=True)
        self.assertFalse(res["env"]["verified"])
        self.assertIn("Metro :8081", " ".join(res["unchecked"]))

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
        self.assertTrue(preflight.check(config(), "quick", fix=False)["env"]["verified"])  # release: no restart
        self.bundle(installed="x " + STAGING + " " + PROD)
        self.assertFalse(preflight.check(config(), "full", fix=False)["env"]["verified"])

    def test_source_is_the_code_the_build_was_made_from(self):
        self.fake_devices()
        self.bundle(installed="x " + STAGING)
        with tmpdir() as repo:
            git = lambda *a: subprocess.run(["git", "-C", repo] + list(a), check=True, stdout=subprocess.PIPE,
                                            universal_newlines=True).stdout.strip()
            git("init", "-q")
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "a")
            with open(os.path.join(repo, "Btn.tsx"), "w") as fh:
                fh.write("hitSlop={4}")
            git("add", "Btn.tsx")
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "b")
            built = git("rev-parse", "HEAD")
            git("rm", "-q", "Btn.tsx")
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c")  # the checkout moved on

            res = preflight.check(config(), "full", project=repo)
            self.assertIsNone(res["source"])  # an installed build of unknown origin: no code lookups
            self.assertIn("code of the installed build is unknown", " ".join(res["unchecked"]))
            self.assertEqual(preflight.check(config(), "full", project=repo, source="built")["source"]["root"], repo)
            src = preflight.check(config(), "full", project=repo, source=built[:8])["source"]
            self.assertEqual(src["ref"], built)
            with open(os.path.join(src["root"], "Btn.tsx")) as fh:
                self.assertEqual(fh.read(), "hitSlop={4}")  # the build's version, not the checkout's
            self.assertEqual(preflight.check(config(), "full", project=repo, source=built)["source"], src)
            with self.assertRaises(FEError):
                preflight._source(repo, "no-such-ref", False)
        self.bundle(metro="x " + STAGING)
        self.assertEqual(preflight.check(config(), "quick", fix=True)["source"]["ref"], "working tree (Metro)")

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

    def test_small_device_without_the_app_is_not_used(self):
        small = Device("android", "emulator-5562", "fresh-eyes-small")
        self.fake_devices(listed=(PIXEL, small))
        with mock.patch.object(devices, "app_installed", lambda dev, app: dev.id != small.id):
            st = preflight.check(config(), "quick", fix=False)["platforms"]["android"]
        self.assertTrue(st["ok"])
        self.assertFalse(st["small_device"])
        self.assertIn("fresh-eyes-small", " ".join(st["problems"]))

    def test_small_device_with_another_build_is_not_used(self):
        small = Device("android", "emulator-5562", "fresh-eyes-small")
        self.fake_devices(listed=(PIXEL, small))
        with mock.patch.object(devices, "app_build", lambda dev, app: ("c" if dev.id == small.id else "b") * 64):
            st = preflight.check(config(), "quick", fix=False)["platforms"]["android"]
        self.assertTrue(st["ok"])
        self.assertEqual(st["build"], "b" * 64)
        self.assertFalse(st["small_device"])
        self.assertIn("another build", " ".join(st["problems"]))

    def test_fix_installs_the_runs_build_on_a_small_device_with_another(self):
        small = Device("android", "emulator-5562", "fresh-eyes-small")
        self.fake_devices(listed=(PIXEL, small))
        builds, installed = {small.id: "c" * 64}, []

        def install(dev, artifact):
            installed.append((dev.id, artifact))
            builds[dev.id] = "b" * 64
        with mock.patch.object(devices, "app_build", lambda dev, app: builds.get(dev.id, "b" * 64)), \
                mock.patch.object(devices, "install", install):
            st = preflight.check(config(), "full", fix=True, project="/p")["platforms"]["android"]
        self.assertEqual(installed, [(small.id, "/p/build/app.apk")])
        self.assertTrue(st["small_device"])

    def test_build_drift_names_the_device_whose_app_changed(self):
        small = Device("android", "emulator-5562", "fresh-eyes-small")
        self.fake_devices(listed=(PIXEL, small))
        pre = preflight.check(config(), "quick", fix=False)
        self.assertEqual(preflight.build_drift(pre), [])
        with mock.patch.object(devices, "app_build", lambda dev, app: None if dev.id == small.id else "b" * 64):
            self.assertEqual(preflight.build_drift(pre), ["android emulator-5562: bbbbbbbbbbbb -> none (not "
                                                          "installed or unreachable)"])

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

    def test_brief_lists_the_consents_the_owner_allowed(self):
        self.fake_devices()
        cfg = dict(config(), consents=[{"text": "Community rules", "button": "Accept"}, {"text": "is an AI"}])
        self.assertIn("may accept: Community rules; is an AI", preflight._brief(preflight.check(cfg, "quick")))
        self.assertIn("may accept: none", preflight._brief(preflight.check(config(), "quick")))

    def test_test_accounts_come_from_the_environment_or_the_keychain(self):
        # 0.5.11: the desktop app's session had no FE_* variables; the orchestrator signed in by its own script
        self.fake_devices()
        cfg = dict(config(), accounts={"viewer": {"email_env": "FE_T_EMAIL", "password_env": "FE_T_PASSWORD"}})
        keychain, asked = set(), []

        def security(cmd, timeout=60, input=None, check=True, redact=()):
            asked.append(cmd)
            return subprocess.CompletedProcess(cmd, 0 if cmd[-1] in keychain else 44, b"", b"")
        with mock.patch.object(preflight, "run", security), mock.patch.dict(os.environ, {"FE_T_EMAIL": "e"}):
            os.environ.pop("FE_T_PASSWORD", None)
            res = preflight.check(cfg, "quick")
            self.assertFalse(res["ok"])
            self.assertTrue(res["platforms"]["android"]["ok"])
            self.assertIn("FE_T_PASSWORD / keychain fresh-eyes-viewer-password", res["unchecked"][0])
            self.assertNotIn("FE_T_EMAIL", res["unchecked"][0])  # the variable is set
            keychain.add("fresh-eyes-viewer-password")
            self.assertTrue(preflight.check(cfg, "quick")["ok"])
        self.assertTrue(asked and all("-w" not in cmd for cmd in asked))  # presence only, no secret read

    def test_full_mode_stops_on_an_app_that_dies_at_launch(self):
        self.fake_devices()
        self.bundle(installed="x " + STAGING)
        self.assertTrue(preflight.check(config(), "full")["platforms"]["android"]["ok"])
        self.state["smoke"] = "the app crashed within 10 s of launch: com.mapbox.maps.MapboxConfigurationException"
        res = preflight.check(config(), "full")
        self.assertFalse(res["ok"])
        self.assertIn("MapboxConfigurationException", " ".join(res["unchecked"]))
        self.assertTrue(preflight.check(config(), "quick")["ok"])  # Metro builds reload: quick has no smoke

    def smoke(self, alive, crash):
        calls = []

        def adb(dev_id, *args, **kwargs):
            calls.append(args)
            return {"pidof": alive}.get(args[1] if len(args) > 1 else "", crash if "-d" in args else "")
        with mock.patch.object(devices, "_adb", adb), mock.patch.object(devices, "launch", lambda d, a: None), \
                mock.patch.object(devices, "terminate", lambda d, a: None), \
                mock.patch.object(preflight.time, "sleep", lambda s: None):
            out = preflight._smoke(PIXEL, "com.example.app")
        self.assertEqual(calls[0], ("logcat", "-b", "crash", "-c"))  # an older crash does not count
        return out

    def test_smoke_reads_the_first_line_of_the_exception(self):
        crash = ("E/AndroidRuntime( 4242): FATAL EXCEPTION: main\n"
                 "E/AndroidRuntime( 4242): Process: com.example.app, PID: 4242\n"
                 "E/AndroidRuntime( 4242): com.mapbox.maps.MapboxConfigurationException: no access token\n"
                 "E/AndroidRuntime( 4242): \tat com.mapbox.maps.MapView.<init>(MapView.kt:1)\n")
        self.assertIsNone(self.smoke("4242", ""))
        self.assertEqual(self.smoke("", crash), "the app crashed within 10 s of launch: "
                                                "com.mapbox.maps.MapboxConfigurationException: no access token")
        self.assertIn("is gone", self.smoke("", ""))  # no crash logged, still not running
        native = "F/libc    ( 4242): Fatal signal 6 (SIGABRT), code -1 in tid 4242 (com.example.app)\n"
        self.assertIn("Fatal signal 6", self.smoke("", native))

    def test_fix_wakes_no_platform_without_a_build_of_the_source(self):
        # 0.5.11: --fix booted two iOS simulators without a build; the run stopped them by hand
        self.fake_devices(listed=())
        booted = []
        with mock.patch.object(devices, "ensure_owned", lambda platform, kind="default": booted.append(kind)):
            with tmpdir() as repo:
                cfg = dict(config(), builds={"full": {"android": {"artifact": "app.apk"}}})
                with mock.patch.object(preflight, "_source", lambda project, ref, metro: {"ref": "x", "root": repo}):
                    st = preflight.check(cfg, "full", fix=True, project=repo, source="built")["platforms"]["android"]
                    self.assertEqual(booted, [])
                    self.assertIn("no build artifact", " ".join(st["problems"]))
                    open(os.path.join(repo, "app.apk"), "w").close()
                    preflight.check(cfg, "full", fix=True, project=repo, source="built")
                    self.assertEqual(booted, ["default"])
                    preflight.check(cfg, "quick", fix=True, project=repo)  # no --source: as before
                    self.assertEqual(booted, ["default", "default"])

    def test_an_empty_gallery_gets_the_fixture_photos(self):
        # 0.5.11: post-create double-submit "done" with "No photos yet": the post could not be sent
        calls = self.fake_devices()
        self.state["photos"] = 0
        res = preflight.check(config(), "quick", fix=False)
        self.assertTrue(res["ok"])
        self.assertIn("no photos", " ".join(res["unchecked"]))
        with mock.patch.object(preflight.time, "sleep", lambda s: None):
            res = preflight.check(config(), "quick", fix=True)
        self.assertIn(("push_photos", 3), calls)
        self.assertEqual(res["platforms"]["android"]["photos"], 3)
        self.assertNotIn("no photos", " ".join(res["unchecked"]))
        self.assertEqual(len(preflight.PHOTOS), 3)
        for path in preflight.PHOTOS:
            with open(path, "rb") as f:
                head = f.read(3)
            self.assertEqual(head, b"\xff\xd8\xff", path)
            self.assertLessEqual(os.path.getsize(path), 50 * 1024, path)

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
