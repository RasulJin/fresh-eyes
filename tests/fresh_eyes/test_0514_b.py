"""0.5.14 part b: what the accounting scripts (coverage, findings, stats, backlog, bench) newly refuse or keep.
Fixtures under fixtures/0514 are copied from the 0.5.13 runs 20261117-1228-full and 20261117-0916-quick."""
import copy
import os
import shutil
import tempfile
import unittest

from tests.fresh_eyes.helpers import FIXTURES
from tests.fresh_eyes.test_coverage import BOTH_OK_MUT_ON, ONE_SCREEN, make_pack
from fe_common import FEError, read_json, write_json
from coverage import plan, set_status

F0514 = os.path.join(FIXTURES, "0514")


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


if __name__ == "__main__":
    unittest.main()
