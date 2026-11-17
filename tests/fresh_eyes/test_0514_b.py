"""0.5.14 part b: what the accounting scripts (coverage, findings, stats, backlog, bench) newly refuse or keep.
Fixtures under fixtures/0514 are copied from the 0.5.13 runs 20261117-1228-full and 20261117-0916-quick."""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.fresh_eyes.helpers import FIXTURES, SCRIPTS
from tests.fresh_eyes.test_coverage import BOTH_OK_MUT_ON, ONE_SCREEN, make_pack
from fe_common import FEError, read_json, write_json
from coverage import _load_findings, plan, set_status, verdict
import backlog
import findings
import stats

F0514 = os.path.join(FIXTURES, "0514")


def _cli(script, *args):
    out = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + list(args), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, check=True)
    return json.loads(out.stdout.decode("utf-8"))


def _root(test):
    root = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, root)
    return root


class MutatingScenarioTest(unittest.TestCase):
    """П-36: a scenario that may write has to write, find it and clean it up."""

    def setUp(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0].update(probes=["scenario"], forms=["Ответить", "Изменить"])
        self.cov = plan(cfg, BOTH_OK_MUT_ON, "full")
        self.cell = next(c for c in self.cov["cells"] if c["probe"] == "scenario" and c["platform"] == "android")
        self.assertTrue(self.cell["mutating"])
        self.pack = make_pack(_root(self), self.cell)

    def _steps(self, steps):
        write_json(os.path.join(self.pack, "steps.json"), steps)
        for s in steps:
            write_json(os.path.join(self.pack, "step-%d" % s["n"], "meta.json"), {"platform": "android"})

    def test_the_read_only_run_of_post_detail_is_not_done(self):
        self._steps(read_json(os.path.join(F0514, "post-detail-scenario-steps.json")))
        with self.assertRaisesRegex(FEError, "missing: wrote, cleaned, found_in. A read-only pass is not this cell"):
            set_status(self.cov, self.cell["id"], "done", evidence=[self.pack])
        set_status(self.cov, self.cell["id"], "blocked", note="no test post to comment on")

    def test_wrote_found_in_on_a_check_step_and_cleaned_make_it_done(self):
        steps = read_json(os.path.join(F0514, "post-detail-scenario-steps.json"))
        steps[0]["wrote"] = "reply «fe test» to the first comment"
        steps[2]["found_in"] = "the post's comments"  # a change step: not where the check looked
        steps[7]["cleaned"] = "reply «fe test» deleted"
        self._steps(steps)
        with self.assertRaisesRegex(FEError, "missing: found_in"):
            set_status(self.cov, self.cell["id"], "done", evidence=[self.pack])
        steps[7]["found_in"] = "post comments and the feed card's counter"
        self._steps(steps)
        set_status(self.cov, self.cell["id"], "done", evidence=[self.pack])
        self.assertEqual(self.cell["status"], "done")

    def test_a_read_only_cell_needs_no_writes(self):
        self.cell["mutating"] = False
        self._steps(read_json(os.path.join(F0514, "post-detail-scenario-steps.json")))
        set_status(self.cov, self.cell["id"], "done", evidence=[self.pack])


class ThemeBothWaysTest(unittest.TestCase):
    """П-37: a light-theme cell cannot be dark, and a dark cell's app theme is the one this capture set."""

    def setUp(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0].update(states=["default", "dark", "keyboard"], probes=["background-foreground"])
        self.cov = plan(cfg, BOTH_OK_MUT_ON, "full")
        self.cells = {(c["state"], c["probe"]): c for c in self.cov["cells"] if c["platform"] == "android"}
        self.root = _root(self)

    def test_a_light_cell_left_on_the_dark_app_theme_is_not_done(self):
        # import after dark-none: keyboard-none and background-foreground at 17-18, default-none at 243
        for key in (("keyboard", "none"), ("default", "background-foreground")):
            cell = self.cells[key]
            with self.assertRaisesRegex(FEError, r"lit_top 17 < 60\) in a light-theme cell"):
                set_status(self.cov, cell["id"], "done", evidence=[make_pack(self.root, cell, lit_top=17)])
            set_status(self.cov, cell["id"], "done", evidence=[make_pack(self.root, cell, lit_top=17)],
                       note="a full-screen video, dark by design")
        for lit in (243, None):  # light, or a capture from before lit_top
            cell = self.cells["default", "none"]
            meta = {"lit_top": lit} if lit else {}
            set_status(self.cov, cell["id"], "done", evidence=[make_pack(self.root, cell, **meta)])
        dark = self.cells["dark", "none"]  # the dark cell is dark at the top too
        set_status(self.cov, dark["id"], "done", evidence=[make_pack(self.root, dark, brightness=60, lit_top=17)])

    def test_a_dark_cell_names_only_the_app_theme_its_capture_set(self):
        dark = self.cells["dark", "none"]
        pack = make_pack(self.root, dark, brightness=60, app_theme_set=None)
        write_json(os.path.join(pack, "theme-note.json"), {"ui_night_mode": "yes", "app_theme": "Как в системе"})
        with self.assertRaisesRegex(FEError, "«Как в системе», but this pack's capture did not set it"):
            set_status(self.cov, dark["id"], "done", evidence=[pack])
        for meta in ({"app_theme_set": "Как в системе"}, {}):  # set in this call, or a capture from before
            pack = make_pack(self.root, dark, brightness=60, **meta)
            write_json(os.path.join(pack, "theme-note.json"), {"ui_night_mode": "yes", "app_theme": "Как в системе"})
            set_status(self.cov, dark["id"], "done", evidence=[pack])
        pack = make_pack(self.root, dark, brightness=60, app_theme_set=None)  # no theme named: nothing to prove
        write_json(os.path.join(pack, "theme-note.json"), {"ui_night_mode": "yes", "app_theme": None})
        set_status(self.cov, dark["id"], "done", evidence=[pack])


class ProbeOnItsScreenTest(unittest.TestCase):
    """П-38: tap-everything over something left open, keyboard-open that never left, rapid-tap with no control."""

    def setUp(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0].update(marker=["guide-composer-wrap"], probes=["tap-everything", "keyboard-open",
                                                                          "rapid-tap"])
        self.cov = plan(cfg, BOTH_OK_MUT_ON, "full")
        self.cells = {c["probe"]: c for c in self.cov["cells"] if c["platform"] == "android"}
        self.root = _root(self)

    def test_ten_taps_that_change_nothing_in_a_row_are_not_done(self):
        cell = self.cells["tap-everything"]
        pack = make_pack(self.root, cell)
        rows = read_json(os.path.join(F0514, "profile-trips-taps.json"))  # quick: a photo viewer left open
        write_json(os.path.join(pack, "taps.json"), rows)
        with self.assertRaisesRegex(FEError, "rows 2-11 changed nothing, 10 in a row"):
            set_status(self.cov, cell["id"], "done", evidence=[pack])
        retapped = [dict(r, n=r["n"] + 11, changed=True) for r in rows[1:]]  # closed it, tapped them again
        write_json(os.path.join(pack, "taps.json"), rows + retapped)
        set_status(self.cov, cell["id"], "done", evidence=[pack])
        write_json(os.path.join(pack, "taps.json"), rows[:3] + [dict(rows[3], changed=True)] + rows[4:6])
        set_status(self.cov, cell["id"], "done", evidence=[pack])  # two in a row, then two: no run of three
        write_json(os.path.join(pack, "taps.json"), rows)
        set_status(self.cov, cell["id"], "done", evidence=[pack], note="counters are labels, no links")

    def test_keyboard_open_proves_it_left_the_screen(self):
        cell = self.cells["keyboard-open"]
        pack = make_pack(self.root, cell)
        shutil.rmtree(os.path.join(pack, "left"))  # quick vio-guide: return snapped without leaving
        with self.assertRaisesRegex(FEError, "needs the snap <pack>/left"):
            set_status(self.cov, cell["id"], "done", evidence=[pack])
        write_json(os.path.join(pack, "left", "meta.json"), {"platform": "android", "source": "uiautomator",
                                                             "time": "2026-11-15T10:00:00"})
        write_json(os.path.join(pack, "left", "nodes.json"), [{"rid": "guide-composer-wrap", "text": "", "label": ""}])
        with self.assertRaisesRegex(FEError, "<pack>/left still shows"):
            set_status(self.cov, cell["id"], "done", evidence=[pack])
        write_json(os.path.join(pack, "left", "nodes.json"), [{"rid": "profile-scroll", "text": "", "label": ""}])
        set_status(self.cov, cell["id"], "done", evidence=[pack])

    def test_rapid_tap_needs_its_single_tap_control_in_any_order(self):
        cell = self.cells["rapid-tap"]
        pack = make_pack(self.root, cell)
        shutil.rmtree(os.path.join(pack, "single"))
        with self.assertRaisesRegex(FEError, "needs the snap <pack>/single"):
            set_status(self.cov, cell["id"], "done", evidence=[pack])
        write_json(os.path.join(pack, "single", "meta.json"), {"platform": "android", "time": "2026-11-15T09:00:00"})
        write_json(os.path.join(pack, "afterback", "meta.json"), {"platform": "android", "time": "2026-11-15T10:00:00"})
        set_status(self.cov, cell["id"], "done", evidence=[pack])  # the control first is fine

    def test_steps_out_of_turn_are_renumbered_never_deleted(self):
        cell = self.cells["keyboard-open"]
        pack = make_pack(self.root, cell)
        write_json(os.path.join(pack, "return", "meta.json"), {"platform": "android", "time": "2026-11-15T10:00:01"})
        write_json(os.path.join(pack, "left", "meta.json"), {"platform": "android", "time": "2026-11-15T10:00:09"})
        with self.assertRaisesRegex(FEError, r"capture.py step --renumber <pack>, never delete a step folder"):
            set_status(self.cov, cell["id"], "done", evidence=[pack])


class ScoreKeepsTheJudgeTest(unittest.TestCase):
    """П-47: a rerun of coverage.py score keeps every note and by the judge wrote."""

    def test_notes_and_by_survive_a_second_score(self):
        run = os.path.join(_root(self), "run")
        shutil.copytree(os.path.join(FIXTURES, "score-run"), run)
        card = read_json(stats.score(run)["out"])
        card["M7"] = {"value": 4, "by": "judge", "note": "post-detail scenario read-only; import left dark"}
        card["M2"]["note"] = "3 disproved by a frame"  # a counted metric annotated, not corrected
        card["M9"].update(value={"hours": 6.8, "idle_gaps": 0}, by="judge", note="owner's pause 17:56-21:51")
        write_json(os.path.join(run, "score.json"), card)
        again = read_json(stats.score(run)["out"])
        self.assertEqual((again["M7"]["value"], again["M7"]["by"], again["M7"]["note"]),
                         (4, "judge", "post-detail scenario read-only; import left dark"))
        self.assertEqual((again["M2"]["value"], again["M2"]["note"]), (card["M2"]["value"], "3 disproved by a frame"))
        self.assertNotIn("by", again["M2"])
        self.assertEqual((again["M9"]["value"], again["M9"]["by"], again["M9"]["note"]),
                         ({"hours": 6.8, "idle_gaps": 0}, "judge", "owner's pause 17:56-21:51"))


class NotApplicableTest(unittest.TestCase):
    """П-48: a list too short to swipe is na, which no verdict calls not done; signed-out screens go first."""

    def test_a_swipe_that_moved_nothing_is_na_and_na_is_not_undone(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0].update(states=["default"], probes=["swipe-inside"])
        cov = plan(cfg, BOTH_OK_MUT_ON, "full")
        cov["cells"] = [c for c in cov["cells"] if c["platform"] == "android"]
        none, swipe = cov["cells"]
        root = _root(self)
        pack = make_pack(root, swipe, moved=0)  # flight-search: the form fits one screen
        with self.assertRaisesRegex(FEError, 'the cell is not applicable: --status na --note "not enough items"'):
            set_status(cov, swipe["id"], "done", evidence=[pack])
        set_status(cov, swipe["id"], "na", note="not enough items: form fits one screen")
        packs = [make_pack(root, none)]
        set_status(cov, none["id"], "done", evidence=packs)
        v = verdict(cov, [], "en", packs)
        self.assertEqual(v["code"], "CLEAN")
        self.assertFalse([r for r in v["reasons"] if "not done" in r])
        self.assertIn("1 cells not applicable: android/profile-trips/default/swipe-inside (not enough items: form "
                      "fits one screen)", v["reasons"])

    def test_signed_out_screens_come_first_in_the_plan(self):
        screens = [{"id": s, "route": "x.tsx", "states": ["default"], "probes": [], "marker": [s]}
                   for s in ("feed", "welcome", "trips", "consent-doc")]
        screens[1]["signed_out"] = screens[3]["signed_out"] = True
        cfg = dict(ONE_SCREEN, screens=screens)
        for mode, chosen in (("full", None), ("quick", ["feed", "consent-doc"])):
            cov = plan(cfg, BOTH_OK_MUT_ON, mode, chosen)
            order = list(dict.fromkeys(c["screen"] for c in cov["cells"] if c["platform"] == "android"))
            self.assertEqual(order, ["welcome", "consent-doc", "feed", "trips"] if mode == "full"
                             else ["consent-doc", "feed"])


class BacklogExtraTest(unittest.TestCase):
    """П-45: open tasks kept outside the backlog (KDD) join the index, and a BUG gets their id."""
    THREAD = ("Поле комментария на «Ветке» отстаёт от поднимающейся клавиатуры: несколько кадров клавиатура "
              "накрывает поле")  # 0.5.13 full thread/findings-1.json#f1, reported without a number
    KDD = "Пост и Ветка: поле комментария на 2-3 кадра под клавиатурой при открытии и останавливается на полпути"

    def test_index_takes_a_file_or_a_command_and_survives_a_broken_one(self):
        root = _root(self)
        tsv = os.path.join(root, "kdd.tsv")
        with open(tsv, "w", encoding="utf-8") as f:
            f.write("#547\t%s\n\nno tab here\n#575\tГид: поле под клавиатурой\n" % self.KDD)
        with open(os.path.join(root, "backlog.md"), "w", encoding="utf-8") as f:
            f.write("### BUG-1 · Старый баг — `open`\n")
        for spec in (tsv, {"cmd": "cat '%s'" % tsv}):
            write_json(os.path.join(root, "config.json"), {"backlog": {"path": "backlog.md", "extra": spec}})
            out = _cli("backlog.py", "index", "--file", os.path.join(root, "backlog.md"), "--out",
                       os.path.join(root, "index.md"), "--config", os.path.join(root, "config.json"))
            self.assertEqual(out, {"entries": 3, "out": os.path.join(root, "index.md")})
            with open(os.path.join(root, "index.md"), encoding="utf-8") as f:
                self.assertEqual(f.read().splitlines(), ["BUG-1 · Старый баг", "#547 · " + self.KDD,
                                                         "#575 · Гид: поле под клавиатурой"])
        write_json(os.path.join(root, "config.json"), {"backlog": {"extra": {"cmd": "exit 3"}}})
        out = _cli("backlog.py", "index", "--file", os.path.join(root, "backlog.md"), "--out",
                   os.path.join(root, "index.md"), "--config", os.path.join(root, "config.json"))
        self.assertEqual(out["entries"], 1)
        self.assertIn("backlog.extra not read", out["extra_error"])

    def test_a_bug_already_open_outside_the_backlog_gets_its_id(self):
        root = _root(self)
        path = os.path.join(root, "android", "thread", "findings-1.json")
        write_json(path, {"findings": [
            {"id": "f1", "kind": "BUG", "screen": "thread", "title": self.THREAD},
            {"id": "f2", "kind": "BUG", "screen": "thread", "title": "Кнопка «Назад» в шапке без подписи"}]})
        out = backlog.match(["BUG-1 · Старый баг - thread", "#547 · " + self.KDD], [path])
        self.assertEqual(out["bugs"], 1)
        self.assertEqual([f.get("backlog_id") for f in read_json(path)["findings"]], ["#547", None])


class ConsequenceTest(unittest.TestCase):
    """П-46: a finding that only follows from another one (same_as, a bare id in its own file) counts once."""

    def test_a_consequence_takes_its_causes_ruling_and_the_verdict_merges_it(self):
        run = _root(self)
        path = os.path.join(run, "android", "vio-guide", "findings-2.json")
        write_json(path, {"findings": [  # 0.5.13 full: «Подробности» picked by the answer's language (f1)
            {"id": "f1", "kind": "BUG", "severity": "medium", "verdict": "confirmed", "platform": "android",
             "screen": "vio-guide", "title": "Vio отвечает не на языке вопроса", "verify_note": "2 of 3 repeats"},
            {"id": "f2", "kind": "BUG", "severity": "low", "platform": "android", "screen": "vio-guide",
             "title": "«Подробности» в английском ответе", "same_as": "f1"}]})
        out = findings.same(run, [path])
        self.assertEqual((out["settled"], out["links"]), (1, [["android/vio-guide/findings-2.json#f2",
                                                                "android/vio-guide/findings-2.json#f1"]]))
        f2 = read_json(path)["findings"][1]
        self.assertEqual((f2["verdict"], f2["severity"], f2["verify_note"]),
                         ("confirmed", "medium", "2 of 3 repeats (same case: android/vio-guide/findings-2.json#f1)"))
        loaded, _ = _load_findings([path], run)
        v = verdict({"cells": [], "unchecked": []}, loaded, "en")
        self.assertIn("1 confirmed medium", v["reasons"])
        self.assertIn("1 duplicate findings merged", v["reasons"])

    def test_a_bare_same_as_ruled_the_other_way_is_a_conflict(self):
        f1 = {"id": "f1", "kind": "BUG", "verdict": "rejected", "_file": "android/a/findings.json", "title": "a"}
        f2 = {"id": "f2", "kind": "BUG", "verdict": "confirmed", "_file": "android/a/findings.json", "title": "b",
              "same_as": "f1", "severity": "low"}
        self.assertEqual(verdict({"cells": [], "unchecked": []}, [f1, f2], "en")["conflicts"],
                         [["android/a/findings.json#f2", "android/a/findings.json#f1"]])


if __name__ == "__main__":
    unittest.main()
