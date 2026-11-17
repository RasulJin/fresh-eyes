"""fresh-eyes 0.5.14, part a (device scripts): П-35 … П-50. All credentials here are fake test values."""
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from tests.fresh_eyes.helpers import SCRIPTS, make_png, tmpdir
import capture
import devices
import fe_common
import nav
import taps
from devices import Device
from fe_common import FEError, read_json, write_json

EMAIL, PASSWORD = "qa.fake@example.test", "Fake.Pass-4821"
ROOT = ('<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0">'
        '<node class="android.widget.FrameLayout" text="" content-desc="" resource-id="" bounds="[0,0][1080,2400]">%s'
        '</node></hierarchy>')


def field(rid, text, y, focused=False, password=False, cls="android.widget.EditText", desc=""):
    return ('<node class="%s" text="%s" content-desc="%s" resource-id="com.example:id/%s" clickable="true" '
            'focused="%s" password="%s" enabled="true" bounds="[60,%d][1020,%d]"/>'
            % (cls, text, desc, rid, str(focused).lower(), str(password).lower(), y, y + 140))


def login(email_text="", password_flag=True, focus="email"):
    """A sign-in screen; the password went into the email field (no password flag) when email_text is it."""
    return ROOT % (field("title", "Вход", 300, cls="android.widget.TextView") +
                   field("email", email_text, 600, focused=focus == "email", desc=email_text.upper()) +
                   field("password", "", 800, focused=focus == "password", password=password_flag) +
                   field("submit", "Войти", 1000, cls="android.widget.Button"))


class Secrets(object):
    """A project folder with a config whose accounts point at fake environment variables."""
    def __enter__(self):
        self.tmp = tmpdir()
        self.repo = self.tmp.__enter__()
        write_json(os.path.join(self.repo, ".fresh-eyes", "config.json"), {"accounts": {
            "viewer": {"email_env": "FE_T_EMAIL", "password_env": "FE_T_PASSWORD"}}})
        self.run = os.path.join(self.repo, ".fresh-eyes", "runs", "r")
        cell = {"id": "android/login/default/tap-everything", "platform": "android", "screen": "login",
                "state": "default", "probe": "tap-everything", "mutating": False, "status": "todo",
                "evidence": [], "note": "", "marker": ["Вход"]}
        write_json(os.path.join(self.run, "coverage.json"), {"run_id": "r", "cells": [cell]})
        write_json(os.path.join(self.run, "preflight.json"), {"mutating_allowed": False})
        self.patches = [mock.patch.dict(os.environ, {"FE_T_EMAIL": EMAIL, "FE_T_PASSWORD": PASSWORD}),
                        mock.patch.object(fe_common, "_secrets", set()),
                        mock.patch.object(fe_common, "_configs", set()),
                        mock.patch.object(fe_common, "_searched", set())]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.__exit__(*exc)

    def leaks(self, text):
        return [s for s in (EMAIL, PASSWORD) if s.lower() in text.lower()]

    def files_leaking(self):
        found = []
        for root, _, files in os.walk(self.run):
            for f in files:
                if f.endswith((".json", ".txt")):
                    with open(os.path.join(root, f), encoding="utf-8") as fh:
                        if self.leaks(fh.read()):
                            found.append(os.path.relpath(os.path.join(root, f), self.run))
        return found


def run_cli(main, argv):
    """stdout of a script's main under fe_common.cli (its error path included) and its exit code."""
    out = io.StringIO()
    with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
        try:
            fe_common.cli(main)
        except SystemExit as e:
            return out.getvalue(), e.code
    return out.getvalue(), 0


class SecretLeakTest(unittest.TestCase):
    """П-35: a password typed into the email field came out in nav.py's screen and its «Visible:» list."""
    DEV = ["--device", "emulator-5560"]

    def device(self, dumps):
        seq = list(dumps)
        return [mock.patch.object(nav, "_dump_uiautomator", lambda dev: seq.pop(0) if len(seq) > 1 else seq[0]),
                mock.patch.object(devices, "keyboard_top", lambda dev, nodes=(): None),
                mock.patch.object(devices, "px_per_dp", lambda dev: 2.625),
                mock.patch.object(devices, "screenshot", lambda dev, dst: make_png(dst, 1080, 2400)),
                mock.patch.object(devices, "tap", lambda dev, x, y: None),
                mock.patch.object(nav.time, "sleep", lambda s: None),
                mock.patch.object(capture.time, "sleep", lambda s: None)]

    def test_screen_tap_miss_and_a_tap_probe_never_show_the_secret(self):
        with Secrets() as s, contextlib.ExitStack() as stack:
            for p in self.device([login(), login(email_text=PASSWORD)]):
                stack.enter_context(p)
            out, code = run_cli(nav.main, ["nav.py", "screen", "--run", s.run] + self.DEV)
            self.assertEqual((code, s.leaks(out)), (0, []))  # the first dump: nothing typed yet
            out, code = run_cli(nav.main, ["nav.py", "screen", "--run", s.run] + self.DEV)
            self.assertEqual((code, s.leaks(out)), (0, []))
            self.assertIn(fe_common.HIDDEN, out)
            out, code = run_cli(nav.main, ["nav.py", "tap", "--text", "нет-такого", "--run", s.run] + self.DEV)
            self.assertEqual(code, 2)
            self.assertIn("Visible", out)
            self.assertEqual(s.leaks(out), [])
            pack = os.path.join(s.run, "android", "login", "default-tap-everything")
            write_json(os.path.join(pack, "nodes.json"), [])
            with mock.patch.object(taps, "settle", lambda dev, before: {"settled": True, "changed": True, "ms": 1}):
                out, code = run_cli(taps.main, ["taps.py", "tap", "--platform", "android", "--device", "e",
                                                "--pack", pack, "--text", "Войти", "--coverage",
                                                os.path.join(s.run, "coverage.json"),
                                                "--cell", "android/login/default/tap-everything"])
            self.assertEqual((code, s.leaks(out)), (0, []))
            self.assertTrue(os.path.isfile(os.path.join(pack, "tap-1", "hierarchy.txt")))
            self.assertEqual(s.files_leaking(), [])

    def test_the_keychain_is_read_when_the_variable_is_unset(self):
        def run(cmd, timeout=60, input=None, check=True, redact=()):
            value = {"fresh-eyes-viewer-password": PASSWORD}.get(cmd[cmd.index("-s") + 1], "")
            return subprocess.CompletedProcess(cmd, 0 if value else 44, (value + "\n").encode(), b"")
        cfg = {"accounts": {"viewer": {"email_env": "FE_T_EMAIL", "password_env": "FE_T_PASSWORD"}}}
        with mock.patch.dict(os.environ, {"FE_T_EMAIL": EMAIL}), mock.patch.object(fe_common, "run", run):
            os.environ.pop("FE_T_PASSWORD", None)
            self.assertEqual(sorted(fe_common.secret_values(cfg)), sorted([EMAIL, PASSWORD]))

        def broken(cmd, **kw):
            raise FEError("command not found: security")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(fe_common, "run", broken):
            self.assertEqual(fe_common.secret_values(cfg), [])

    def type_secret(self, xml, env):
        sent = []

        def run(cmd, timeout=60, input=None, check=True, redact=()):
            sent.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        with Secrets() as s, contextlib.ExitStack() as stack:
            for p in self.device([xml]) + [mock.patch.object(devices, "_require_owned", lambda dev, what: None),
                                           mock.patch.object(devices, "adb_path", lambda: "adb"),
                                           mock.patch.object(fe_common, "run", run)]:
                stack.enter_context(p)
            out, code = run_cli(nav.main, ["nav.py", "type", "--env", env, "--keychain", "x"] + self.DEV)
            return out, code, [c for c in sent if "input" in c], s.leaks(out)

    def test_a_secret_goes_only_into_a_focused_field_a_password_only_into_a_password_field(self):
        out, code, typed, leaks = self.type_secret(login(focus="password"), "FE_T_PASSWORD")
        self.assertEqual((code, leaks, len(typed)), (0, [], 1))
        self.assertIn(nav.TYPED, out)
        out, code, typed, leaks = self.type_secret(login(email_text=EMAIL), "FE_T_PASSWORD")  # email has focus
        self.assertEqual((code, typed, leaks), (2, [], []))
        self.assertIn("no password flag", out)
        out, code, typed, leaks = self.type_secret(login(password_flag=False, focus="password"), "FE_T_PASSWORD")
        self.assertEqual((code, typed), (2, []))
        out, code, typed, leaks = self.type_secret(login(focus="none"), "FE_T_EMAIL")
        self.assertEqual((code, typed), (2, []))
        self.assertIn("no text field has focus", out)
        out, code, typed, leaks = self.type_secret(login(focus="email"), "FE_T_EMAIL")
        self.assertEqual((code, leaks, len(typed)), (0, [], 1))

    def test_the_maestro_tree_and_ios_say_which_field_hides_its_text(self):
        from hierarchy import normalize
        ios = '{"axElement": {"elementType": 1, "frame": {"X": 0, "Y": 0, "Width": 400, "Height": 800}, ' \
              '"children": [{"elementType": 50, "hasFocus": true, "frame": {"X": 10, "Y": 10, "Width": 300, ' \
              '"Height": 40}}]}}'
        node = normalize(ios, "xctest")[1]
        self.assertEqual((node["cls"], node["password"]), ("SecureTextField", True))
        nav.secret_field([node], password=True)


class LitTopTest(unittest.TestCase):
    """П-37: three light-state import cells were snapped in the app's dark theme (top half 17-18, not 243)."""
    def frame(self, d, bg, rects=()):
        return make_png(os.path.join(d, "f.png"), 360, 800, bg=bg, rects=rects)

    def test_dark_light_keyboard_and_a_sheet_over_a_dimmed_screen(self):
        from pixels import lit_top
        with tmpdir() as d:
            self.assertLess(lit_top(self.frame(d, (18, 18, 20)), [360, 800]), 60)
            light = self.frame(d, (243, 243, 243), [(0, 300, 360, 500, (20, 20, 20), 0)])  # dark keyboard area
            self.assertGreater(lit_top(light, [360, 800], kb_top=300), 200)
            self.assertLess(lit_top(light, [360, 800]), 200)  # without knowing the keyboard it counts
            dimmed = self.frame(d, (40, 40, 40), [(0, 500, 360, 300, (250, 250, 250), 0)])
            self.assertLess(lit_top(dimmed, [360, 800]), 60)
            self.assertGreater(lit_top(dimmed, [360, 800], panels=[[0, 500, 360, 300]]), 200)

    def test_snap_writes_lit_top_and_only_the_theme_this_call_set(self):
        xml = ROOT % (field("title", "Импорт", 300, cls="android.widget.TextView") +
                      ('<node class="android.view.ViewGroup" text="" content-desc="" resource-id="" '
                       'bounds="[0,1500][1080,2400]"/>'))
        with tmpdir() as d, mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml), \
                mock.patch.object(devices, "screenshot", lambda dev, dst: make_png(dst, 1080, 2400, bg=(17, 17, 18))), \
                mock.patch.object(devices, "px_per_dp", lambda dev: 2.625), \
                mock.patch.object(devices, "keyboard_top", lambda dev, nodes=(): None), \
                mock.patch.object(devices, "night_mode", lambda dev: "Night mode: no"):
            pack = os.path.join(d, "android", "import", "default-none")
            capture.snap("android", "e", pack)
            meta = read_json(os.path.join(pack, "meta.json"))
            self.assertEqual((meta["app_theme_set"], meta["lit_top"] < 60), (None, True))
            capture.snap("android", "e", pack, app_theme="Тёмная")
            self.assertEqual(read_json(os.path.join(pack, "meta.json"))["app_theme_set"], "Тёмная")


TAP_CELL = {"id": "android/feed/default/tap-everything", "platform": "android", "screen": "feed", "state": "default",
            "probe": "tap-everything", "mutating": False, "status": "todo", "evidence": [], "note": "",
            "marker": ["Лента"]}


def node(i, text, y, rid="", cls="android.widget.TextView", clickable=True, **kw):
    return dict({"id": "n%d" % i, "parent": None, "depth": 1, "cls": cls, "text": text, "label": "", "rid": rid,
                 "bounds": [0, y, 1080, 120], "clickable": clickable, "focused": False, "enabled": True}, **kw)


def run_folder(d, cells=(TAP_CELL,)):
    write_json(os.path.join(d, "coverage.json"), {"run_id": "r", "cells": list(cells)})
    write_json(os.path.join(d, "preflight.json"), {"mutating_allowed": False})
    return os.path.join(d, "coverage.json")


def tap_patches(dumps, taps_=None, settle_changed=True):
    seq = list(dumps)
    return [mock.patch.object(taps.nav, "hierarchy", lambda dev: (seq.pop(0) if len(seq) > 1 else seq[0],
                                                                  "uiautomator")),
            mock.patch.object(devices, "keyboard_top", lambda dev, nodes=(): None),
            mock.patch.object(devices, "screenshot", lambda dev, dst: make_png(dst, 108, 240)),
            mock.patch.object(devices, "tap", lambda dev, x, y: (taps_ if taps_ is not None else []).append((x, y))),
            mock.patch.object(taps, "settle", lambda dev, before: {"settled": True, "changed": settle_changed,
                                                                    "ms": 5})]


class OwnScreenTest(unittest.TestCase):
    """П-38: tap-everything went on tapping a photo viewer left open over the screen (ten «no change»)."""
    def test_a_tap_off_the_cells_screen_is_refused(self):
        rows = [node(1, "Фото 1 из 3", 400), node(2, "Закрыть", 700)]
        with tmpdir() as d, contextlib.ExitStack() as stack:
            cov = run_folder(d)
            pack = os.path.join(d, "android", "feed", "default-tap-everything")
            for p in tap_patches([rows]) + [mock.patch.object(taps.capture, "snap", lambda *a, **kw: 0)]:
                stack.enter_context(p)
            argv = ["taps.py", "tap", "--platform", "android", "--device", "e", "--pack", pack, "--text", "Закрыть",
                    "--coverage", cov, "--cell", TAP_CELL["id"]]
            out, code = run_cli(taps.main, argv)
            self.assertEqual(code, 2)
            self.assertIn("not this screen: Лента missing", out)
            self.assertFalse(os.path.exists(os.path.join(pack, "taps.json")))
        taps.on_screen(rows + [node(3, "", 0, rid="app:id/feed-лента-tab")], ["лента"])  # id, any case


class RenumberTest(unittest.TestCase):
    """П-38: steps out of order are renumbered by snap time, never deleted (0.5.13 quick removed two)."""
    def test_steps_follow_their_snap_time_and_steps_json_follows_them(self):
        with tmpdir() as pack:
            for n, t in ((1, "10:00:00"), (2, "10:00:30"), (3, "10:00:10"), (5, "10:01:00")):
                write_json(os.path.join(pack, "step-%d" % n, "meta.json"), {"time": "2026-11-17T" + t, "n": n})
            write_json(os.path.join(pack, "steps.json"), [{"n": k, "did": "did %d" % k} for k in (1, 2, 3, 4, 5)])
            out = subprocess.run([sys.executable, os.path.join(SCRIPTS, "capture.py"), "step", "--renumber", pack],
                                 stdout=subprocess.PIPE, check=True).stdout
            self.assertEqual(json.loads(out)["renumbered"], {"step-2": "step-4", "step-3": "step-2", "step-4": "step-3"})
            self.assertEqual([read_json(os.path.join(pack, "step-%d" % k, "meta.json"))["n"] for k in (1, 2, 4, 5)],
                             [1, 3, 2, 5])
            self.assertFalse(os.path.exists(os.path.join(pack, "step-3")))  # step 3 of steps.json has no snap
            self.assertEqual([(s["n"], s["did"]) for s in read_json(os.path.join(pack, "steps.json"))],
                             [(1, "did 1"), (2, "did 3"), (3, "did 4"), (4, "did 2"), (5, "did 5")])
            self.assertEqual(capture.renumber(pack), {})  # in order now


if __name__ == "__main__":
    unittest.main()
