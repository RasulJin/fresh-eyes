"""fresh-eyes 0.5.14, part a (device scripts): П-35 … П-50. All credentials here are fake test values."""
import contextlib
import io
import os
import subprocess
import sys
import unittest
from unittest import mock

from tests.fresh_eyes.helpers import make_png, tmpdir
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


if __name__ == "__main__":
    unittest.main()
