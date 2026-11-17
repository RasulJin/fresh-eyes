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
from datetime import datetime, timezone
from unittest import mock

from tests.fresh_eyes.helpers import FIXTURES, SCRIPTS
from tests.fresh_eyes.test_coverage import BOTH_OK_MUT_ON, ONE_SCREEN, make_pack
from fe_common import FEError, read_json, write_json
from coverage import NOTES_MARK, _load_findings, next_cells, pack_groups, plan, set_status, verdict
import backlog
import bench
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


class RequestsMergeTest(unittest.TestCase):
    """П-40: findings.py requests makes one entry of a screen's requests that ask for one repeat."""

    def test_one_repeat_is_asked_once(self):
        run = _root(self)
        real = read_json(os.path.join(F0514, "requests-open.json"))  # 0.5.13 full, done/failed taken off
        for screen, entries in real.items():
            write_json(os.path.join(run, "android", screen, "requests.json"), entries)
        write_json(os.path.join(run, "android", "trips", "requests.json"),
                   real["trips"] + [{"do": "redo-cell", "cell": "android/trips/default/none", "why": "x"},
                                    dict(real["trips"][1], done="android/trips/default-swipe-inside-recheck")])
        out = _cli("findings.py", "requests", "--run", run)
        self.assertEqual(out["merged"], 3)  # trip-map: f1 resnap twice, f2 d50 twice; settings: two dialogs
        trip_map = read_json(os.path.join(run, "android", "trip-map", "requests.json"))
        self.assertEqual([(r["pack"].split("/")[-1], len(r.get("merged", []))) for r in trip_map],
                         [("dark-none", 1), ("d50", 1), ("drag-1", 0)])
        self.assertEqual(trip_map[0]["why"], real["trip-map"][0]["why"] + "\n" + real["trip-map"][1]["why"])
        self.assertEqual(trip_map[0]["times"], 2)
        settings = read_json(os.path.join(run, "android", "settings", "requests.json"))
        self.assertEqual(len(settings), 2)
        self.assertIn("«Удалить аккаунт»", settings[1]["why"])
        trips = read_json(os.path.join(run, "android", "trips", "requests.json"))
        self.assertEqual(len(trips), 5)  # two crops of other boxes, a redo-cell and a done entry stay apart
        self.assertEqual(_cli("findings.py", "requests", "--run", run), {"merged": 0, "files": []})

    def test_a_merged_request_still_names_its_finding(self):
        run = _root(self)
        folder = os.path.join(run, "android", "trip-map")
        write_json(os.path.join(folder, "findings.json"), {"findings": [
            {"id": "f1", "kind": "BUG", "confidence": 0.3, "title": "a"},
            {"id": "f2", "kind": "BUG", "confidence": 0.3, "title": "b"}]})
        ask = {"pack": "android/trip-map/dark-none", "do": "resnap", "probe": "none", "from": "findings.json"}
        write_json(os.path.join(folder, "requests.json"), [dict(ask, finding="f1"), dict(ask, finding="f2")])
        findings.requests(run)
        loaded, _ = _load_findings([os.path.join(folder, "findings.json")], run)
        self.assertEqual([f["_requested"] for f in loaded], [True, True])


class OrchestratorContextTest(unittest.TestCase):
    """П-42: subagents' notes gathered by a script; the card says how long the orchestrator ran and how full."""

    def test_notes_gather_under_the_orchestrators_own_lines(self):
        run = _root(self)
        with open(os.path.join(run, "orchestrator-notes.md"), "w", encoding="utf-8") as f:
            f.write("# Заметки оркестратора\n\n- preflight: сборка уже стояла\n")
        for name, text in (("post-detail-navigator", "- scenario: reply sent and deleted\n"),  # <screen>-<who>
                           ("import-verifier", "- dark: Тёмная\n")):
            os.makedirs(os.path.join(run, "notes"), exist_ok=True)
            with open(os.path.join(run, "notes", name + ".md"), "w", encoding="utf-8") as f:
                f.write(text)
        for _ in range(2):  # a second gathering rebuilds, never repeats
            out = _cli("coverage.py", "notes", "--run", run)
        self.assertEqual(out["notes"], 2)
        with open(out["out"], encoding="utf-8") as f:
            self.assertEqual(f.read(), "# Заметки оркестратора\n\n- preflight: сборка уже стояла\n\n%s\n\n"
                                       "## import-verifier\n\n- dark: Тёмная\n\n## post-detail-navigator\n\n"
                                       "- scenario: reply sent and deleted\n" % NOTES_MARK)

    def test_the_card_counts_the_orchestrators_turns_and_context(self):
        root = _root(self)
        run = os.path.join(root, "run")
        shutil.copytree(os.path.join(FIXTURES, "score-run"), run)
        t0 = 1700000000  # the run's first file; report.md an hour later
        for name in os.listdir(run):
            os.utime(os.path.join(run, name), (t0, t0) if name != "report.md" else (t0 + 3600,) * 2)

        def turn(dt, mid, fresh, read, wrote):
            return {"type": "assistant", "timestamp": datetime.fromtimestamp(t0 + dt, timezone.utc).isoformat(),
                    "message": {"id": mid, "content": [], "usage": {
                        "input_tokens": fresh, "cache_read_input_tokens": read, "cache_creation_input_tokens": wrote}}}
        os.makedirs(os.path.join(root, "projects", "-repo"))
        with open(os.path.join(root, "projects", "-repo", "s1.jsonl"), "w") as f:
            for rec in (turn(-60, "m0", 1, 90000, 0), turn(5, "m1", 3, 114000, 2000), turn(6, "m1", 3, 114000, 2000),
                        turn(50, "m2", 2, 240000, 9000)):
                f.write(json.dumps(rec) + "\n")
        with mock.patch.object(stats, "PROJECTS", os.path.join(root, "projects")):
            out = stats.score(run, "s1")
        expect = {"turns": 2, "context_start": 116003, "context_end": 249002}  # m0 is before the run
        self.assertEqual(out["orchestrator"], expect)
        self.assertEqual(read_json(out["out"])["orchestrator"], expect)
        self.assertEqual(stats.score(run)["orchestrator"], expect)  # no session id: what was counted stays
        os.remove(out["out"])
        self.assertIsNone(stats.score(run)["orchestrator"])  # never counted: not known

    def test_a_run_resumed_in_new_sessions_is_scored_over_all_of_them(self):
        # acceptance 0.5.14: П-42 splits a full run into sessions and step 11 passed only the last one; a rerun
        # without a session turned M10 from 190 M into null
        root = _root(self)
        run = os.path.join(root, "run")
        shutil.copytree(os.path.join(FIXTURES, "score-run"), run)
        t0 = 1700000000
        for name in os.listdir(run):
            os.utime(os.path.join(run, name), (t0, t0) if name != "report.md" else (t0 + 5 * 3600,) * 2)

        def turn(dt, mid, read, *tools):
            return {"type": "assistant", "timestamp": datetime.fromtimestamp(t0 + dt, timezone.utc).isoformat(),
                    "message": {"id": mid, "content": [{"type": "tool_use", "name": n} for n in tools],
                                "usage": {"input_tokens": 1, "cache_read_input_tokens": read}}}
        projects = os.path.join(root, ".claude", "projects", "-repo")
        for rel, recs in (("s1.jsonl", [turn(10, "a1", 1000), turn(20, "a2", 50000, "TaskStop")]),
                          ("s2.jsonl", [turn(3 * 3600, "b1", 2000), turn(3 * 3600 + 60, "b2", 9000)]),  # 3 h later
                          ("s2/subagents/agent-x.jsonl", [turn(3 * 3600 + 30, "x1", 700)])):
            os.makedirs(os.path.dirname(os.path.join(projects, rel)), exist_ok=True)
            with open(os.path.join(projects, rel), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in recs)

        def score(*args):
            out = subprocess.run([sys.executable, os.path.join(SCRIPTS, "coverage.py"), "score", "--run", run] +
                                 list(args), env=dict(os.environ, HOME=root), stdout=subprocess.PIPE, check=True)
            return json.loads(out.stdout.decode("utf-8")), read_json(os.path.join(run, "score.json"))
        out, card = score("--session", "s2", "--session", "s1,s2")
        self.assertEqual(card["M10"]["value"], {"total": 62700, "orchestrator": 62000})
        self.assertEqual(card["M9"]["value"]["idle_gaps"], 0)  # the 3 h between the sessions is no idle gap
        self.assertEqual(card["M12"]["value"]["stops"], 1)
        self.assertEqual(out["orchestrator"], {"turns": 4, "context_start": 1001, "context_end": 9001})
        for args in ((), ("--session", "s1,s3")):  # no session, or one not found: what was counted stays
            out, again = score(*args)
            self.assertFalse(out["transcript"])
            self.assertEqual([again[k]["value"] for k in ("M9", "M10", "M12")] + [again["orchestrator"]],
                             [card[k]["value"] for k in ("M9", "M10", "M12")] + [card["orchestrator"]])


def _imp(fid, screen, title, **kw):
    return dict(dict(id=fid, kind="IMP", screen=screen, platform="android", severity="low", title=title,
                     verdict="pending"), **kw)


class ImpCopiesTest(unittest.TestCase):
    """П-44: one case's IMPs get one number in imp.md, and a rephrased copy of an open entry is known."""
    TOGGLE = ["Переключатель темы в боковом меню — одинокая луна без подписи и без вида переключателя",
              "Переключатель темы — одинокая иконка луны/солнца без подписи «Тёмная тема» внизу пустой панели",
              "Переключатель темы внизу меню — одинокая луна без подписи «Тёмная тема»",
              "Переключатель темы — одинокая луна без подписи и без вида переключателя, далеко под списком"]

    def _run(self):  # titles from the 0.5.13 full run
        run = _root(self)
        files = {
            "feed/findings-2.json": [_imp("f3", "side-menu", self.TOGGLE[0])],  # the side menu seen from the feed
            "side-menu/findings-1.json": [_imp("f3", "side-menu", self.TOGGLE[1])],
            "side-menu/findings-2.json": [_imp("f1", "side-menu", self.TOGGLE[2],
                                               same_as="android/side-menu/findings-1.json#f3")],  # the verifier's
            "side-menu/findings-3.json": [_imp("f2", "side-menu", self.TOGGLE[3])],
            "trips/findings-1.json": [_imp("f1", "trips", "«⋯» на большой карточке почти не видна на светлом фото"),
                                      _imp("f2", "trips", "На маленьком экране большая карточка с кнопками занимает "
                                                          "почти весь экран, под ней видна одна строка")],
            "trip-detail/findings-1.json": [_imp("f6", "trip-detail", "«Маршрут по дням» — голый список между двумя "
                                                                       "карточками",
                                                 same_as="android/trip-detail/findings-2.json#f2")],
            "trip-detail/findings-2.json": [_imp("f2", "trip-detail", "«Маршрут по дням» — единственный раздел без "
                                                                       "карточки")],
            "vio-guide/findings-1.json": [_imp("f2", "vio-guide", "Карточка «Дешёвые рейсы на следующий месяц» "
                                                                   "переносит одно слово «месяц» и выше соседних")],
            "vio-guide/findings-3.json": [_imp("f6", "vio-guide", "Карточка быстрого старта «Дешёвые рейсы на "
                                                                   "следующий месяц» в две строки — выше трёх соседних")]}
        paths = []
        for rel, items in files.items():
            paths.append(os.path.join(run, "android", rel))
            write_json(paths[-1], {"findings": items})
        return run, paths

    def test_same_links_one_cases_imps_in_any_folder_to_its_first(self):
        run, paths = self._run()
        out = findings.same(run, paths)
        self.assertEqual((out["links"], out["settled"]), ([], 0))  # IMP copies take no ruling, run-log skips them
        same_as = {"%s#%s" % (os.path.relpath(p, run), f["id"]): f.get("same_as") for p in paths
                   for f in read_json(p)["findings"]}
        self.assertEqual(same_as, {
            "android/feed/findings-2.json#f3": None,
            "android/side-menu/findings-1.json#f3": "android/feed/findings-2.json#f3",
            "android/side-menu/findings-2.json#f1": "android/side-menu/findings-1.json#f3",
            "android/side-menu/findings-3.json#f2": "android/feed/findings-2.json#f3",
            "android/trips/findings-1.json#f1": None,  # «⋯» names an element the small-screen card does not
            "android/trips/findings-1.json#f2": None,
            "android/trip-detail/findings-1.json#f6": "android/trip-detail/findings-2.json#f2",
            "android/trip-detail/findings-2.json#f2": None,  # the verifier's link stays, no loop back
            "android/vio-guide/findings-1.json#f2": None,
            "android/vio-guide/findings-3.json#f6": "android/vio-guide/findings-1.json#f2"})
        self.assertEqual(findings.same(run, paths)["linked"], 0)

    def test_imp_md_gives_a_group_one_number_and_its_open_entry(self):
        run, paths = self._run()
        findings.same(run, paths)
        index = ["IMP-312 · Вторая карточка быстрого старта переносится на две строки и выше соседних - vio-guide"]
        out = backlog.match(index, paths, os.path.join(run, "imp.md"), "ru")
        self.assertEqual((out["new"], out["known"], out["matched"]), (4, 1, 2))  # 95 joins its twin's IMP-312
        self.assertEqual([f["backlog_id"] for f in read_json(paths[-2])["findings"]], ["IMP-312"])
        with open(out["out"], encoding="utf-8") as f:
            text = f.read()
        self.assertIn("## 1. %s\nside-menu · android · low · `android/feed/findings-2.json#f3`\n"
                      "+ side-menu · `android/side-menu/findings-1.json#f3`\n"
                      "+ side-menu · `android/side-menu/findings-2.json#f1`\n"
                      "+ side-menu · `android/side-menu/findings-3.json#f2`\n" % self.TOGGLE[0], text)
        self.assertIn("## 4. «Маршрут по дням» — единственный раздел без карточки\n"
                      "trip-detail · android · low · `android/trip-detail/findings-2.json#f2`\n"
                      "+ trip-detail · `android/trip-detail/findings-1.json#f6`\n", text)
        self.assertIn("# Уже в бэклоге: 1\n\n- Карточка «Дешёвые рейсы на следующий месяц» переносит одно слово "
                      "«месяц» и выше соседних — IMP-312 «Вторая карточка быстрого старта переносится на две строки и "
                      "выше соседних» · `android/vio-guide/findings-1.json#f2` `android/vio-guide/findings-3.json#f6`",
                      text)


class BenchInScoreTest(unittest.TestCase):
    """П-49: the seeded bench a full run was gated on is in its score.json and stays there."""

    def test_bench_score_goes_into_the_runs_score_json(self):
        run = os.path.join(_root(self), "run")
        shutil.copytree(os.path.join(FIXTURES, "score-run"), run)  # gated before navigation: no score.json yet
        scores = {"SEED-%d" % k: {"hits": h, "attempts": 3, "caught": h >= 2} for k, h in enumerate((3, 2, 2, 1, 3))}
        self.assertEqual(bench.record(run, scores), {"caught": 4, "of": 5})
        card = read_json(stats.score(run)["out"])  # the run scored again after the bench
        self.assertEqual(card["bench"], {"caught": 4, "of": 5})
        self.assertIn("M1", card)


class TwoQueuesTest(unittest.TestCase):
    """П-41: plan splits a platform's cells between the main and the small device; next hands out one queue."""
    NEAR = {"post-detail": ["thread"], "trips": ["trip-detail"], "trip-detail": ["trip-map", "trip-schedule"],
            "welcome": ["consent-doc"]}

    def _cfg(self):
        ids = ["feed", "post-detail", "thread", "trips", "trip-detail", "trip-map", "trip-schedule", "vio-guide",
               "settings", "welcome", "consent-doc"]
        return dict(ONE_SCREEN, screens=[{"id": s, "route": "x.tsx", "marker": [s], "neighbors": self.NEAR.get(s, []),
                                          "states": ["default", "dark", "small-screen"], "probes": ["tap-everything"]}
                                         for s in ids])

    def test_only_small_screen_cells_go_to_the_small_device(self):
        pre = {"platforms": {"android": {"ok": True, "small_device": True}, "ios": {"ok": True}}}
        cov = plan(self._cfg(), pre, "full", passes=2)
        android = [c for c in cov["cells"] if c["platform"] == "android"]
        self.assertEqual({(c["state"] == "small-screen", c["queue"]) for c in android},
                         {(True, "small"), (False, "main")})  # a post and its thread: both main
        self.assertEqual({c["queue"] for c in cov["cells"] if c["platform"] == "ios"}, {"main"})  # no small iPhone
        path = os.path.join(_root(self), "coverage.json")
        write_json(path, cov)
        small = _cli("coverage.py", "next", "--cov", path, "--queue", "small", "--limit", "500")
        self.assertEqual(sorted(small), sorted(c["id"] for c in android if c["state"] == "small-screen"))

    def test_without_a_small_device_every_cell_is_main(self):
        pre = {"platforms": {"android": {"ok": True}}}
        cov = plan(self._cfg(), pre, "full")
        self.assertEqual({c["queue"] for c in cov["cells"]}, {"main"})
        self.assertEqual(len(next_cells(cov, queue="small")), 0)
        self.assertEqual(len(next_cells(cov, queue="main", limit=500)), len(cov["cells"]))


class ProofSnapsTest(unittest.TestCase):
    """Part c's left/ and restored/ snaps prove a step to `set`; no reviewer judges them as the screen."""

    def test_packs_leave_out_left_and_restored(self):
        screen = os.path.join(_root(self), "android", "vio-guide")
        for sub in ("", "kb-show", "left", "return", "dark-restored-x"):  # a pack merely named so stays
            write_json(os.path.join(screen, "default-keyboard-open", sub, "meta.json"),
                       {"source": "uiautomator", "time": "2026-11-15T10:00:00"})
            with open(os.path.join(screen, "default-keyboard-open", sub, "screen.png"), "wb") as f:
                f.write(b"png")
        write_json(os.path.join(screen, "dark-none", "restored", "meta.json"), {"time": "2026-11-15T10:00:00"})
        with open(os.path.join(screen, "dark-none", "restored", "screen.png"), "wb") as f:
            f.write(b"png")
        write_json(os.path.join(screen, "dark-none", "meta.json"), {"time": "2026-11-15T10:00:00"})
        with open(os.path.join(screen, "dark-none", "screen.png"), "wb") as f:
            f.write(b"png")
        groups = pack_groups(screen)
        self.assertEqual(sorted(os.path.relpath(r, screen) for g in groups for r in g),
                         ["dark-none", "default-keyboard-open", "default-keyboard-open/dark-restored-x",
                          "default-keyboard-open/kb-show", "default-keyboard-open/return"])


if __name__ == "__main__":
    unittest.main()
