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
REAL_SLOW = devices.animations_slow
_DEVICE = [mock.patch.object(devices, "animations_slow", lambda dev: False),  # a test never reads a real device
           mock.patch.object(nav, "_dump_maestro", lambda dev: (_ for _ in ()).throw(FEError("no Maestro in tests")))]


def setUpModule():
    for p in _DEVICE:
        p.start()


def tearDownModule():
    for p in reversed(_DEVICE):
        p.stop()
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

    def test_logcat_never_carries_the_secret(self):
        # acceptance 0.5.14: an app's own log line with the typed credentials went into logcat.txt in clear
        logcat = "\n".join(["W/ReactNativeJS( 4242): login failed for %s pw=%s" % (EMAIL.upper(), PASSWORD),
                            "E/AndroidRuntime( 5001): Process: com.example.app, PID: 5001",
                            "E/AndroidRuntime( 5001): at signIn(%s)" % PASSWORD])

        def adb(dev_id, *args, **kw):
            return "4242\n" if args[:2] == ("shell", "pidof") else logcat
        with Secrets() as s, mock.patch.object(devices, "_adb", adb):
            pack = os.path.join(s.run, "android", "login", "default-none")
            out, code = run_cli(capture.main, ["capture.py", "logs", "--platform", "android", "--device", "e",
                                               "--out", pack, "--package", "com.example.app"])
            self.assertEqual(code, 0)
            with open(os.path.join(pack, "logcat.txt"), encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(s.leaks(text), [])
            self.assertEqual(text.count(fe_common.HIDDEN), 3)
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


class SwipeTapTest(unittest.TestCase):
    """П-39: side-menu#1f1 was confirmed with no tap.json: swipe-tap took only a point and wrote none."""
    def run_swipe_tap(self, target):
        events = []
        before_nodes = [node(1, "Посты", 300, label="Посты"), node(2, "", 300, rid="app:id/trips-tab",
                                                                    label="Поездки", depth=3)]
        before_nodes[1]["bounds"] = [540, 250, 540, 120]

        def snap(platform, dev, out, **kw):
            if os.path.basename(out) == "before":
                write_json(os.path.join(out, "nodes.json"), before_nodes)
                write_json(os.path.join(out, "meta.json"), {"screen_wh": [1080, 2400]})
            return 0

        def around(platform, dev, out, action, settle=2.5, ceiling_ms=None, call=None):
            events.append(action)
            call()
        with tmpdir() as d:
            cell = dict(TAP_CELL, id="android/feed/default/drag-interrupt", probe="drag-interrupt")
            cov = run_folder(d, [cell])
            write_json(cov, {"run_id": "r", "cells": [cell], "motion_ceiling_ms": 280})
            pack = os.path.join(d, "android", "feed", "default-drag-interrupt")
            argv = ["taps.py", "swipe-tap", "--platform", "android", "--device", "e", "--from", "700,1200",
                    "--to", "100,1200", "--pack", pack, "--coverage", cov, "--cell", cell["id"]] + target
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(taps.capture, "snap", snap), mock.patch.object(taps.capture, "around", around), \
                    mock.patch.object(devices, "swipe_then_tap",
                                      lambda dev, *a, **kw: events.append(("tap", a[4], a[5], kw["delay_ms"]))):
                taps.main()
            return events, read_json(os.path.join(pack, "swipe-tap", "tap.json"))

    def test_the_target_is_found_before_the_swipe_and_tap_json_written(self):
        events, rec = self.run_swipe_tap(["--label", "Поездки"])
        self.assertEqual(events[1], ("tap", 810, 310, 280))
        self.assertEqual((rec["point"], rec["bounds"], rec["node"]["rid"], rec["delay_ms"], rec["by"]),
                         ([810, 310], [540, 250, 540, 120], "app:id/trips-tab", 280, "label"))
        self.assertIn("--label", events[0])  # around.json keeps the whole command
        events, rec = self.run_swipe_tap(["--x", "900", "--y", "300", "--delay-ms", "120"])
        self.assertEqual((events[1], rec["node"]["label"], rec["by"]), (("tap", 900, 300, 120), "Поездки", "point"))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_swipe_tap(["--id", "trips-tab", "--x", "1", "--y", "2"])

    def test_rows_continue_after_the_last_row_and_any_tap_folder(self):
        with tmpdir() as pack:
            rows = [{"n": k} for k in (1, 2, 3)]
            for k in (1, 2, 3):
                os.makedirs(os.path.join(pack, "tap-%d" % k))
            os.makedirs(os.path.join(pack, "tap-2-recheck"))
            self.assertEqual(taps.next_row(pack, rows), 4)
            os.makedirs(os.path.join(pack, "tap-4"))  # a tap that failed before its row
            self.assertEqual(taps.next_row(pack, rows), 5)
            self.assertEqual(taps.next_row(os.path.join(pack, "new"), []), 1)


class ConditionsTest(unittest.TestCase):
    """П-40: rechecks ran from another pager page (quick profile-trips) and at normal speed (trip-map)."""
    def test_snap_and_recording_carry_the_conditions_a_recheck_repeats(self):
        tab = ('<node class="android.widget.Button" text="" content-desc="Поездки" resource-id="" selected="true" '
               'clickable="true" bounds="[540,250][1080,370]"/>')
        xml = ROOT % (field("title", "Профиль", 100, cls="android.widget.TextView") + tab)
        with tmpdir() as d, mock.patch.object(nav, "_dump_uiautomator", lambda dev: xml), \
                mock.patch.object(devices, "screenshot", lambda dev, dst: make_png(dst, 108, 240)), \
                mock.patch.object(devices, "px_per_dp", lambda dev: 2.625), \
                mock.patch.object(devices, "keyboard_top", lambda dev, nodes=(): None), \
                mock.patch.object(devices, "night_mode", lambda dev: "Night mode: yes"), \
                mock.patch.object(devices, "animations_slow", lambda dev: True), \
                mock.patch.object(capture, "analyze", lambda *a, **kw: {"flags": [], "peak_change": 0.1}), \
                mock.patch.object(devices, "start_recording", lambda dev, dst, limit_s=60: mock.Mock(started=0)), \
                mock.patch.object(devices, "stop_recording", lambda rec: None), \
                mock.patch.object(capture.time, "sleep", lambda s: None):
            pack = os.path.join(d, "android", "profile-trips", "default-drag-interrupt-recheck")
            capture.snap("android", "e", pack, app_theme="Тёмная")
            self.assertEqual(read_json(os.path.join(pack, "meta.json"))["conditions"],
                             {"slow_animations": True, "app_theme": "Тёмная", "start_page": "Поездки"})
            rec = os.path.join(d, "android", "trip-map", "default-back-during-animation", "d100")
            capture.around("android", "e", rec, ["true"], settle=0)
            self.assertEqual(read_json(os.path.join(rec, "around.json"))["conditions"], {"slow_animations": True})

    def test_slow_means_any_scale_above_one(self):
        own = Device("android", "emulator-5560", "fresh-eyes-pixel")
        for out, slow in (("1.0\n5\n1.0\n", True), ("1.0\n1.0\n1\n", False), ("null\nnull\nnull\n", False),
                          ("0.0\n0.0\n0.0\n", False)):
            with mock.patch.object(devices, "_shell", lambda dev, cmd, check=True, timeout=60: out):
                self.assertEqual(REAL_SLOW(own), slow, out)
        self.assertFalse(REAL_SLOW(Device("ios", "AAA", "fresh-eyes iPhone 17")))


class TapAllTest(unittest.TestCase):
    """П-43: tap-everything in one call instead of a model turn per tap."""
    NODES = [node(0, "Лента", 100, clickable=False), node(1, "Row A", 400), node(2, "Удалить пост", 600),
             node(3, "", 800, rid="app:id/comment", cls="android.widget.EditText"), node(4, "Отправить", 1000),
             node(5, "Row B", 1200), node(6, "подпись", 1400, clickable=False)]

    def run_all(self, extra=(), lost_after=None, config=None):
        events, dumps = [], [0]

        def hierarchy(dev):
            dumps[0] += 1
            nodes = self.NODES if lost_after is None or dumps[0] <= lost_after else self.NODES[1:]
            return nodes, "uiautomator"

        def snap(platform, dev, out, **kw):
            focused = "comment" in events[-1][1]
            write_json(os.path.join(out, "nodes.json"), self.NODES)
            write_json(os.path.join(out, "meta.json"), {"keyboard_up": focused})
            make_png(os.path.join(out, "screen.png"), 108, 240, bg=(30, 30, 30) if focused else (255, 255, 255))
            return 0

        def tap(dev, x, y):
            hit = next(n for n in self.NODES if n["bounds"][1] <= y < n["bounds"][1] + 120)
            events.append(("tap", nav._name(hit)))
        with tmpdir() as d, contextlib.ExitStack() as stack:
            cov = run_folder(d)
            if config:
                write_json(os.path.join(d, ".fresh-eyes", "config.json"), config)
            pack = os.path.join(d, "android", "feed", "default-tap-everything")
            write_json(os.path.join(pack, "nodes.json"), self.NODES)
            write_json(os.path.join(pack, "som.json"), [{"n": k + 1, "node_ids": [n["id"]], "bbox": n["bounds"]}
                                                        for k, n in enumerate(self.NODES)])
            make_png(os.path.join(pack, "screen.png"), 108, 240)
            write_json(os.path.join(pack, "taps.json"), [{"n": 1, "som": 6, "label": "Row B"}])
            full = make_png(os.path.join(d, "full.png"), 1080, 2400)
            for p in tap_patches([]) + [mock.patch.object(taps.nav, "hierarchy", hierarchy),
                                        mock.patch.object(devices, "screenshot", lambda dev, dst: full),
                                        mock.patch.object(devices, "tap", tap),
                                        mock.patch.object(devices, "back", lambda dev: events.append(("back",))),
                                        mock.patch.object(taps.time, "sleep", lambda s: None),
                                        mock.patch.object(taps.capture, "snap", snap)]:
                stack.enter_context(p)
            out, code = run_cli(taps.main, ["taps.py", "all", "--platform", "android", "--device", "e", "--pack",
                                            pack, "--coverage", cov, "--cell", TAP_CELL["id"]] + list(extra))
            return json.loads(out), code, events, read_json(os.path.join(pack, "taps.json"))

    def test_every_clickable_mark_once_inputs_first_destructive_left_out_back_only_when_it_left(self):
        out, code, events, rows = self.run_all()
        self.assertEqual(code, 0)
        self.assertEqual(events, [("tap", "app:id/comment"), ("back",), ("tap", "Row A")])
        self.assertEqual([(r["n"], r.get("som")) for r in rows], [(1, 6), (2, 4), (3, 2)])
        self.assertEqual(sorted(s["label"] for s in out["skipped"]), ["Отправить", "Удалить пост"])
        self.assertEqual((out["left"], out["stopped"]), (0, None))
        out, code, events, rows = self.run_all(["--limit", "1", "--skip", "comment"])
        self.assertEqual((events, out["left"]), ([("tap", "Row A")], 0))

    def test_the_screens_skip_tap_labels_are_never_pressed(self):
        # a chip of Vio's chat sends a message: config screens[].skip_tap keeps tap-everything off it
        out, code, events, rows = self.run_all(config={"screens": [{"id": "feed", "skip_tap": ["row a"]},
                                                                   {"id": "other", "skip_tap": ["comment"]}]})
        self.assertEqual(events, [("tap", "app:id/comment"), ("back",)])
        self.assertIn({"som": 2, "label": "Row A", "why": "skipped: config skip_tap"}, out["skipped"])

    def test_it_stops_where_the_cells_screen_is_lost(self):
        out, code, events, rows = self.run_all(lost_after=1)
        self.assertEqual(code, 2)
        self.assertIn("not this screen", out["stopped"])
        self.assertEqual((len(out["tapped"]), out["left"]), (1, 1))


class KeyboardStateTest(unittest.TestCase):
    """П-49: trip-wizard's fifth suggestion was cut mid-row by «Дальше» with the keyboard up and went in as IMP 76:
    the footer reaches 63 px under the keyboard, so «pinned on the keyboard» never matched."""
    def test_a_footer_partly_behind_the_keyboard_cuts_the_suggestions(self):
        from geometry import run_all
        nodes = [node(0, "", 0, cls="android.widget.FrameLayout", clickable=False),
                 node(1, "Paris", 287, cls="android.widget.EditText", focused=True),
                 node(2, "Paris, Кентукки, США", 1047, cls="android.widget.Button"),
                 node(3, "Paris, Иллинойс, США", 1213, cls="android.widget.Button"),
                 node(4, "", 1323, rid="wizard-footer", cls="android.view.ViewGroup", clickable=False),
                 node(5, "Дальше", 1355, cls="android.widget.Button")]
        for n, b in zip(nodes, ([0, 0, 1080, 2400], [172, 287, 811, 108], [61, 1047, 960, 166], [61, 1213, 960, 110],
                                [0, 1323, 1080, 257], [47, 1355, 986, 132])):
            n["bounds"] = b
        found = [c for c in run_all(nodes, {"screen_wh": [1080, 2400], "keyboard_top": 1517}) if c["check"] == "under_bar"]
        self.assertEqual([c["node_ids"] for c in found], [["n4", "n3"]])
        self.assertIn("Дальше", found[0]["detail"])


def fake_adb(answers):
    """fe_common.run answering adb shell commands by substring; records the shell lines."""
    shells = []

    def run(cmd, timeout=60, input=None, check=True, redact=()):
        line = " ".join(cmd)
        shells.append(cmd[-1])
        out = next((v for k, v in dict({"emu avd name": "fresh-eyes-pixel\nOK\n"}, **answers).items() if k in line), "")
        return subprocess.CompletedProcess(cmd, 0, out.encode(), b"")
    return run, shells


class ToolDetailsTest(unittest.TestCase):
    """П-50: the small tool faults behind 0.5.13's rejects and retakes."""
    OWN = Device("android", "emulator-5560", "fresh-eyes-pixel")

    def test_a_hung_dump_goes_to_the_maestro_cli_after_one_short_wait(self):
        seen, dumps = [], []

        def run(cmd, timeout=60, **kw):
            seen.append(timeout)
            raise FEError("timed out after %ss: %s" % (timeout, " ".join(cmd)))
        with mock.patch.object(nav.fe_common, "run", run), mock.patch.object(nav.devices, "adb_path", lambda: "adb"):
            with self.assertRaises(FEError):
                nav._dump_uiautomator(self.OWN)
        self.assertEqual(seen, [nav.DUMP_TIMEOUT_S])
        self.assertLessEqual(nav.DUMP_TIMEOUT_S, 20)

        def hung(dev):
            dumps.append(1)
            raise FEError("timed out after 20s: adb exec-out uiautomator dump")
        with mock.patch.object(nav, "_dump_uiautomator", hung), \
                mock.patch.object(nav, "_dump_maestro", lambda dev: '{"attributes": {}, "children": [{"attributes": '
                                  '{"bounds": "[0,0][1080,2400]", "text": "Добро пожаловать"}}]}'), \
                mock.patch.object(nav.time, "sleep", lambda s: None):
            nodes, source = nav.hierarchy(self.OWN)
        self.assertEqual((source, len(dumps), nodes[0]["text"]), ("maestro", 1, "Добро пожаловать"))

    def test_slow_animations_sets_all_three_scales(self):
        run, shells = fake_adb({})
        with mock.patch.object(fe_common, "run", run):
            devices.apply_setting(self.OWN, "slow-animations")
        line = [s for s in shells if "settings put" in s][0]
        for key in ("window_animation_scale 5", "transition_animation_scale 5", "animator_duration_scale 5"):
            self.assertIn(key, line)

    def test_a_drag_never_starts_where_the_system_takes_it(self):
        run, shells = fake_adb({"wm size": "Physical size: 1080x2400\n"})
        with mock.patch.object(fe_common, "run", run):
            for x, y in ((1040, 1200), (40, 1200), (500, 2380), (500, 20)):
                with self.assertRaisesRegex(FEError, "within 48 px", msg=(x, y)):
                    devices.swipe(self.OWN, x, y, 100, 1200)
            with self.assertRaises(FEError):
                devices.swipe_then_tap(self.OWN, 1040, 1200, 100, 1200, 540, 300)
            with self.assertRaises(FEError):
                devices.drag_then_back(self.OWN, 1050, 1200, 100, 1200)
            self.assertFalse([s for s in shells if "input" in s])
            devices.swipe(self.OWN, 900, 1200, 100, 1200)
        self.assertIn("input swipe 900 1200 100 1200 300", shells)

    def test_frames_as_wide_as_a_recheck_asks(self):
        from fe_common import image_size, run as real_run
        from motion import extract_frames
        with tmpdir() as d:
            video = os.path.join(d, "v.mkv")
            real_run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=90x200:d=0.2:r=10",
                      "-c:v", "ffv1", video])
            self.assertEqual(image_size(extract_frames(video, os.path.join(d, "a"))[0]), (136, 300))
            self.assertEqual(image_size(extract_frames(video, os.path.join(d, "b"), width=540)[0]), (540, 1200))
            seen = []
            with mock.patch.object(capture, "analyze", lambda *a: seen.append(a[-1]) or {"flags": [], "peak_change": 0}), \
                    mock.patch.object(devices, "start_recording", lambda dev, dst, limit_s=60: mock.Mock(started=0)), \
                    mock.patch.object(devices, "stop_recording", lambda rec: None), \
                    mock.patch.object(capture.time, "sleep", lambda s: None):
                capture.around("android", "e", os.path.join(d, "p"), ["true"], settle=0, frame_width=540)
            self.assertEqual(seen, [540])
        out = subprocess.run([sys.executable, os.path.join(SCRIPTS, "capture.py"), "around", "--help"],
                             stdout=subprocess.PIPE).stdout
        self.assertIn(b"--frame-width", out)

    def test_sharp_corners_come_from_the_panel_not_its_inner_scroll_view(self):
        from pixels import corner_candidates
        nodes = [node(0, "", 0, cls="android.widget.FrameLayout", clickable=False),
                 node(1, "", 0, rid="profile-drawer-panel", cls="android.view.ViewGroup", clickable=False, parent="n0"),
                 node(2, "", 40, cls="android.widget.ScrollView", clickable=False, parent="n1")]
        for n, b in zip(nodes, ([0, 0, 360, 800], [0, 0, 266, 800], [0, 40, 266, 725])):
            n["bounds"] = b
        with tmpdir() as d:
            shot = make_png(os.path.join(d, "s.png"), 360, 800, bg=(60, 60, 60),
                            rects=[(0, 0, 266, 800, (250, 250, 250), 14)])
            self.assertEqual(corner_candidates(shot, nodes, [360, 800]), [])
            square = make_png(os.path.join(d, "q.png"), 360, 800, bg=(60, 60, 60),
                              rects=[(0, 0, 266, 800, (250, 250, 250), 0)])
            self.assertEqual([c["node_ids"] for c in corner_candidates(square, nodes, [360, 800])], [["n1"]])


class QueueHeartbeatTest(unittest.TestCase):
    """Two navigators on one platform (coverage.py queues): each one's own heartbeat, read by nav.py idle --queue."""
    def test_a_queue_beats_its_own_file_and_idle_reads_it(self):
        with tmpdir() as run:
            write_json(os.path.join(run, "coverage.json"), {"cells": [{"id": "android/feed/default/none",
                                                                        "status": "todo"}]})
            fe_common.heartbeat(os.path.join(run, "android", "feed", "default-none"), "android", "capture.py snap",
                                "small")
            beats = sorted(os.listdir(os.path.join(run, "android")))
            self.assertEqual(beats, ["heartbeat", "heartbeat-small"])
            old = os.path.join(run, "android", "heartbeat-small")
            os.utime(old, (os.path.getmtime(old) - 700,) * 2)
            idle = subprocess.run([sys.executable, os.path.join(SCRIPTS, "nav.py"), "idle", "--run", run,
                                   "--queue", "small"], stdout=subprocess.PIPE, check=True).stdout
            self.assertGreaterEqual(json.loads(idle)["idle_s"], 700)
            with mock.patch.object(nav.time, "sleep", lambda s: None):
                self.assertEqual(nav.wait_idle(run, "android", 0, ["android/feed/default/none"], queue="main")["state"],
                                 "stuck")  # the main queue never beat
        for script, sub in (("capture.py", "snap"), ("taps.py", "all"), ("nav.py", "screen")):
            out = subprocess.run([sys.executable, os.path.join(SCRIPTS, script), sub, "--help"],
                                 stdout=subprocess.PIPE).stdout
            self.assertIn(b"--queue", out, script)
        bad = subprocess.run([sys.executable, os.path.join(SCRIPTS, "nav.py"), "idle", "--run", "r", "--queue",
                              "../x"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(bad.returncode, 2)


if __name__ == "__main__":
    unittest.main()
