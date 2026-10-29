import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests.fresh_eyes.helpers import tmpdir
from fe_common import FEError, read_json, write_json
from coverage import (PROBES, REQUIRED_SUBPACKS, SCENARIO_KINDS, STATES, _load_findings, coverage_map, next_cells,
                      plan, prune, screens_from_diff, set_status, verdict)

ONE_SCREEN = {"language": "ru", "app": {"expo_root": "apps/mobile"}, "hooks": {},
              "screens": [{"id": "profile-trips", "route": "app/(tabs)/profile.tsx", "states": ["default", "dark"],
                           "probes": ["rapid-tap", "edit-delete-restore"], "neighbors": []}]}
BOTH_OK_MUT_OFF = {"platforms": {"android": {"ok": True}, "ios": {"ok": True}}, "mutating_allowed": False}
BOTH_OK_MUT_ON = dict(BOTH_OK_MUT_OFF, mutating_allowed=True)
MUT_SCREEN = copy.deepcopy(ONE_SCREEN)
MUT_SCREEN["screens"][0]["probes"] = ["edit-delete-restore"]


def full_steps(forms=()):
    """A scenario that passes `set`: every kind, both interruptions, each form of the screen."""
    steps = ([{"kind": k} for k in sorted(SCENARIO_KINDS - {"interrupt"})] +
             [{"kind": "interrupt", "how": "stop"}, {"kind": "interrupt", "how": "offline"}] +
             [{"kind": "task", "via": f} for f in forms])
    return [dict(s, n=i + 1) for i, s in enumerate(steps)]


def make_pack(root, cell, sub=True, **meta):
    path = os.path.join(root, cell["platform"], cell["screen"], "%s-%s" % (cell["state"], cell["probe"]))
    base = {"platform": cell["platform"]}
    if cell["probe"] == "swipe-inside":  # snapped with --before, and the swipe moved the list
        base.update(before=path + "-before", moved=0.4)
    write_json(os.path.join(path, "meta.json"), dict(base, **meta))
    if sub and cell["probe"] in REQUIRED_SUBPACKS:
        write_json(os.path.join(path, REQUIRED_SUBPACKS[cell["probe"]], "meta.json"), {"platform": cell["platform"]})
    if sub and cell["probe"] == "scenario":
        steps = full_steps(cell.get("forms", []))
        write_json(os.path.join(path, "steps.json"), steps)
        for s in steps:
            write_json(os.path.join(path, "step-%d" % s["n"], "meta.json"), {"platform": cell["platform"]})
        with open(os.path.join(path, "logcat.txt"), "w") as f:
            f.write("# no warnings\n")
    return path


def done_all(cov, root):
    """Mark every cell done with a real pack; returns the pack paths (what a reviewer would list)."""
    packs = []
    for c in cov["cells"]:
        packs.append(make_pack(root, c))
        set_status(cov, c["id"], "done", evidence=[packs[-1]])
    return packs


def finding(sev, verdict_="confirmed", kind="BUG"):
    return {"kind": kind, "severity": sev, "verdict": verdict_, "title": "x"}


class PlanTest(unittest.TestCase):
    def test_registry_shapes(self):
        self.assertTrue(PROBES["edit-delete-restore"]["mutating"])
        self.assertEqual(PROBES["hardware-back-keyboard"]["platforms"], ["android"])
        self.assertIn("keyboard", STATES)

    def test_plan_two_platforms_mutating_off(self):
        cov = plan(ONE_SCREEN, BOTH_OK_MUT_OFF, "full")
        self.assertEqual(len(cov["cells"]), 6)
        self.assertTrue(all(c["status"] == "todo" and not c["mutating"] for c in cov["cells"]))
        self.assertIn("ios/profile-trips/default/rapid-tap", [c["id"] for c in cov["cells"]])
        self.assertTrue(any("edit-delete-restore" in u["what"] for u in cov["unchecked"]))

    def test_plan_mutating_on_includes_probe(self):
        pre = dict(BOTH_OK_MUT_OFF, mutating_allowed=True)
        cov = plan(ONE_SCREEN, pre, "full")
        self.assertEqual(len(cov["cells"]), 8)
        self.assertTrue(any(c["mutating"] for c in cov["cells"]))

    def test_plan_ios_missing_is_unchecked(self):
        pre = {"platforms": {"android": {"ok": True}, "ios": {"ok": False}}, "mutating_allowed": False}
        cov = plan(ONE_SCREEN, pre, "full")
        self.assertTrue(all(c["platform"] == "android" for c in cov["cells"]))
        self.assertTrue(any(u["what"].startswith("ios") for u in cov["unchecked"]))

    def test_plan_respects_config_platforms(self):
        cfg = dict(ONE_SCREEN, platforms=["android"])
        pre = {"platforms": {"android": {"ok": True}}, "mutating_allowed": True}
        cov = plan(cfg, pre, "full")
        self.assertFalse(any(u["what"].startswith("ios") for u in cov["unchecked"]))

    def test_journeys_touching_chosen_screens_are_planned(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["journeys"] = [{"id": "reply", "steps": "x", "screens": ["profile-trips"], "mutating": True},
                           {"id": "other", "steps": "y", "screens": ["feed"], "mutating": False}]
        pre = dict(BOTH_OK_MUT_OFF, platforms={"android": {"ok": True}}, mutating_allowed=True)
        cfg["platforms"] = ["android"]
        ids = [c["id"] for c in plan(cfg, pre, "quick", ["profile-trips"])["cells"]]
        self.assertIn("android/journey/reply/journey", ids)
        self.assertNotIn("android/journey/other/journey", ids)

    def test_mutating_journey_unchecked_without_staging(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["journeys"] = [{"id": "reply", "steps": "x", "screens": ["profile-trips"], "mutating": True}]
        cov = plan(cfg, BOTH_OK_MUT_OFF, "full")
        self.assertFalse(any("journey" in c["id"] for c in cov["cells"]))
        self.assertTrue(any("reply" in u["what"] for u in cov["unchecked"]))

    def test_platform_only_probe_skipped_on_other_platform(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0].update(states=["default"], probes=["hardware-back-keyboard"])
        ids = [c["id"] for c in plan(cfg, BOTH_OK_MUT_OFF, "full")["cells"]]
        self.assertIn("android/profile-trips/default/hardware-back-keyboard", ids)
        self.assertNotIn("ios/profile-trips/default/hardware-back-keyboard", ids)

    def test_seed_states_unchecked_without_hook(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0].update(states=["default", "empty", "small-screen"], probes=[])
        cov = plan(cfg, BOTH_OK_MUT_OFF, "full")
        self.assertEqual({c["state"] for c in cov["cells"]}, {"default"})
        whats = " ".join(u["what"] for u in cov["unchecked"])
        self.assertIn("empty", whats)
        self.assertIn("small-screen", whats)

    def test_quick_lists_neighbors_and_extra_screens_as_unchecked(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0]["neighbors"] = ["profile-posts"]
        for sid in ["profile-posts", "feed", "a", "b", "c"]:
            cfg["screens"].append({"id": sid, "route": sid, "states": ["default"], "probes": []})
        cov = plan(cfg, BOTH_OK_MUT_OFF, "quick", ["profile-trips"])
        self.assertEqual({c["screen"] for c in cov["cells"]}, {"profile-trips"})
        self.assertIn("profile-posts: all states", [u["what"] for u in cov["unchecked"]])
        cov = plan(cfg, BOTH_OK_MUT_OFF, "quick", ["profile-trips", "profile-posts"])
        self.assertEqual({c["screen"] for c in cov["cells"]}, {"profile-trips", "profile-posts"})
        cov = plan(cfg, BOTH_OK_MUT_OFF, "quick", ["feed", "a", "b", "c", "profile-trips"])
        self.assertNotIn("profile-trips", {c["screen"] for c in cov["cells"]})
        self.assertIn("over the quick limit", " ".join(u["why"] for u in cov["unchecked"]))
        self.assertEqual(len({c["screen"] for c in plan(cfg, BOTH_OK_MUT_OFF, "quick", ["feed", "a", "b", "c",
                              "profile-trips"], max_screens=5)["cells"]}), 5)

    def test_quick_without_screens_plans_only_rechecks(self):
        cov = plan(ONE_SCREEN, BOTH_OK_MUT_OFF, "quick", recheck=["BUG-3"])
        self.assertEqual({c["screen"] for c in cov["cells"]}, {"recheck-BUG-3"})
        self.assertEqual(plan(ONE_SCREEN, BOTH_OK_MUT_OFF, "quick")["cells"], [])

    def test_unknown_screen_raises(self):
        with self.assertRaises(FEError):
            plan(ONE_SCREEN, BOTH_OK_MUT_OFF, "quick", ["nope"])


class StatusTest(unittest.TestCase):
    def test_set_unknown_cell_raises(self):
        cov = plan(ONE_SCREEN, BOTH_OK_MUT_OFF, "full")
        with self.assertRaises(FEError):
            set_status(cov, "android/none/default/none", "done")

    def test_set_bad_status_raises(self):
        cov = plan(ONE_SCREEN, BOTH_OK_MUT_OFF, "full")
        with self.assertRaises(FEError):
            set_status(cov, cov["cells"][0]["id"], "finished")

    def test_done_needs_a_real_unblocked_pack(self):
        cov = plan(ONE_SCREEN, BOTH_OK_MUT_OFF, "full")
        cell = cov["cells"][0]
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        with self.assertRaises(FEError):
            set_status(cov, cell["id"], "done")
        with self.assertRaises(FEError):
            set_status(cov, cell["id"], "done", evidence=[os.path.join(root, "missing")])
        with self.assertRaises(FEError):
            set_status(cov, cell["id"], "done", evidence=[make_pack(root, cell, blocked="no hierarchy")])
        failed = make_pack(root, dict(cell, probe="around"))
        write_json(os.path.join(failed, "around.json"), {"action_exit": 2})
        with self.assertRaises(FEError):
            set_status(cov, cell["id"], "done", evidence=[failed])
        self.assertEqual(cell["status"], "todo")
        set_status(cov, cell["id"], "blocked", note="no hierarchy")  # other statuses need no evidence

    def test_done_needs_the_probes_required_snap(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0]["probes"] = ["rapid-tap", "keyboard-open", "search", "tap-outside"]
        cov = plan(cfg, BOTH_OK_MUT_OFF, "full")
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        for cell in [c for c in cov["cells"] if c["platform"] == "android" and c["probe"] != "none"]:
            if cell["probe"] in REQUIRED_SUBPACKS:
                with self.assertRaisesRegex(FEError, REQUIRED_SUBPACKS[cell["probe"]]):
                    set_status(cov, cell["id"], "done", evidence=[make_pack(root, cell, sub=False)])
            set_status(cov, cell["id"], "done", evidence=[make_pack(root, cell)])
            self.assertEqual(cell["status"], "done")

    def test_swipe_inside_pack_must_show_what_the_swipe_moved(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0]["probes"] = ["swipe-inside"]
        cov = plan(cfg, BOTH_OK_MUT_OFF, "full")
        cell = next(c for c in cov["cells"] if c["probe"] == "swipe-inside")
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        pack = make_pack(root, cell)
        write_json(os.path.join(pack, "meta.json"), {"platform": "android"})
        with self.assertRaisesRegex(FEError, "--before"):
            set_status(cov, cell["id"], "done", evidence=[pack])
        write_json(os.path.join(pack, "meta.json"), {"platform": "android", "before": pack + "-b", "moved": 0})
        with self.assertRaisesRegex(FEError, "moved nothing"):
            set_status(cov, cell["id"], "done", evidence=[pack])
        set_status(cov, cell["id"], "done", evidence=[pack], note="the grid fits, it does not scroll")
        self.assertEqual(cell["status"], "done")

    def test_tap_outside_drags_each_listed_surface_with_the_overlay_open(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0].update(probes=["tap-outside"], drag=["шторка маршрута"])
        cov = plan(cfg, BOTH_OK_MUT_OFF, "full")
        cell = next(c for c in cov["cells"] if c["probe"] == "tap-outside")
        self.assertEqual(cell["drag"], ["шторка маршрута"])
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        pack = make_pack(root, cell)
        with self.assertRaisesRegex(FEError, "шторка маршрута"):
            set_status(cov, cell["id"], "done", evidence=[pack])
        write_json(os.path.join(pack, "drag-1", "meta.json"), {"platform": "android"})
        set_status(cov, cell["id"], "done", evidence=[pack])
        self.assertEqual(cell["status"], "done")

    def test_long_list_is_a_seeded_state(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0]["states"] = ["default", "long-list"]
        self.assertIn("android/profile-trips/long-list", " ".join(u["what"] for u in plan(cfg, BOTH_OK_MUT_ON, "full")["unchecked"]))
        cfg["hooks"] = {"seed": "npm run seed"}
        self.assertIn("android/profile-trips/long-list/none", [c["id"] for c in plan(cfg, BOTH_OK_MUT_ON, "full")["cells"]])

    def test_two_passes_plan_every_cell_twice_with_own_packs(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0].update(probes=["rapid-tap", "scenario"], marker=["Мои поездки"], forms=["Новая поездка"])
        one = plan(cfg, BOTH_OK_MUT_ON, "quick", screens=["profile-trips"], recheck=["BUG-7"])
        two = plan(cfg, BOTH_OK_MUT_ON, "quick", screens=["profile-trips"], recheck=["BUG-7"], passes=2)
        rechecks = [c for c in one["cells"] if c["screen"].startswith("recheck-")]
        self.assertEqual(len(two["cells"]), 2 * len(one["cells"]) - len(rechecks))  # a replay runs once
        second = [c for c in two["cells"] if c.get("pass") == 2]
        self.assertTrue(all(c["id"].endswith("#2") and c["status"] == "todo" for c in second))
        scen = next(c for c in second if c["probe"] == "scenario")
        self.assertEqual((scen["marker"], scen["forms"], scen["mutating"]), (["Мои поездки"], ["Новая поездка"], True))
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        rapid = next(c for c in second if c["probe"] == "rapid-tap" and c["platform"] == "android")
        pack = make_pack(root, rapid) + "-p2"  # the second pass snaps into its own pack
        shutil.copytree(make_pack(root, rapid), pack)
        set_status(two, rapid["id"], "done", evidence=[pack])
        self.assertEqual(next(c for c in two["cells"] if c["id"] == rapid["id"][:-2])["status"], "todo")
        self.assertIn("rapid-tap android #2", coverage_map(two))

    def test_return_snap_must_show_the_keyboard_closed(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0]["probes"] = ["keyboard-open"]
        cov = plan(cfg, BOTH_OK_MUT_OFF, "full")
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        cell = next(c for c in cov["cells"] if c["probe"] == "keyboard-open")
        pack = make_pack(root, cell)
        write_json(os.path.join(pack, "return", "meta.json"), {"platform": cell["platform"], "keyboard_up": True})
        with self.assertRaisesRegex(FEError, "keyboard is still up"):
            set_status(cov, cell["id"], "done", evidence=[pack])
        write_json(os.path.join(pack, "return", "meta.json"), {"platform": cell["platform"], "keyboard_up": False})
        set_status(cov, cell["id"], "done", evidence=[pack])
        self.assertEqual(cell["status"], "done")

    def test_scenario_needs_every_step_kind_and_logcat(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0]["probes"] = ["scenario"]
        cov = plan(cfg, BOTH_OK_MUT_OFF, "full")
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        cells = {c["platform"]: c for c in cov["cells"] if c["probe"] == "scenario"}
        android = cells["android"]
        pack = make_pack(root, android, sub=False)
        with self.assertRaisesRegex(FEError, "change/check/interrupt/invalid/task"):
            set_status(cov, android["id"], "done", evidence=[pack])
        write_json(os.path.join(pack, "steps.json"), [{"n": 1, "kind": "task"}, {"n": 2, "kind": "change"},
                                                      {"n": 3, "kind": "check"}])
        with self.assertRaisesRegex(FEError, "no interrupt/invalid step"):  # the steps a navigator skips
            set_status(cov, android["id"], "done", evidence=[pack])
        steps = [{"n": i, "kind": k} for i, k in enumerate(SCENARIO_KINDS)]
        write_json(os.path.join(pack, "steps.json"), steps)  # a tab switch passed off as the interruption
        with self.assertRaisesRegex(FEError, "how=offline and how=stop"):
            set_status(cov, android["id"], "done", evidence=[pack])
        write_json(os.path.join(pack, "steps.json"), full_steps())
        with self.assertRaisesRegex(FEError, "logcat"):
            set_status(cov, android["id"], "done", evidence=[pack])
        set_status(cov, android["id"], "done", evidence=[make_pack(root, android)])
        ios = cells["ios"]  # no app logs and no automated offline on iOS: steps with a stop are enough
        pack = make_pack(root, ios, sub=False)
        write_json(os.path.join(pack, "steps.json"), steps + [{"kind": "interrupt", "how": "stop"}])
        set_status(cov, ios["id"], "done", evidence=[pack])
        self.assertEqual([android["status"], ios["status"]], ["done", "done"])

    def test_scenario_goes_through_each_form_of_the_screen(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0].update(probes=["scenario"], forms=["Задать параметры"])
        cov = plan(cfg, BOTH_OK_MUT_OFF, "full")
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        cell = next(c for c in cov["cells"] if c["probe"] == "scenario" and c["platform"] == "android")
        self.assertEqual(cell["forms"], ["Задать параметры"])
        pack = make_pack(root, dict(cell, forms=[]))  # every kind, but the form never opened
        with self.assertRaisesRegex(FEError, "«Задать параметры»"):
            set_status(cov, cell["id"], "done", evidence=[pack])
        steps = read_json(os.path.join(pack, "steps.json"))
        steps.append({"n": 99, "kind": "task", "via": "Задать параметры", "did": "not run"})  # named, never snapped
        write_json(os.path.join(pack, "steps.json"), steps)
        with self.assertRaisesRegex(FEError, "own snap"):
            set_status(cov, cell["id"], "done", evidence=[pack])
        write_json(os.path.join(pack, "step-99", "meta.json"), {"platform": "android"})
        set_status(cov, cell["id"], "done", evidence=[pack])
        self.assertEqual(cell["status"], "done")

    def test_scenario_writes_only_when_the_backend_allows_it(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0]["probes"] = ["scenario"]
        for pre, writes in ((BOTH_OK_MUT_ON, True), (BOTH_OK_MUT_OFF, False)):
            cells = [c for c in plan(cfg, pre, "full")["cells"] if c["probe"] == "scenario"]
            self.assertEqual([c["mutating"] for c in cells], [writes, writes])  # planned either way

    def test_on_screen_pack_must_show_the_screens_marker(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0]["marker"] = ["Мои поездки", "trips-list"]
        cfg["screens"][0]["probes"] = ["rapid-tap"]
        cov = plan(cfg, BOTH_OK_MUT_OFF, "full")
        self.assertTrue(all(c["marker"] == ["Мои поездки", "trips-list"] for c in cov["cells"]))
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        cell = {c["id"]: c for c in cov["cells"] if c["platform"] == "android"}

        def pack(c, **node):
            path = make_pack(root, c)
            write_json(os.path.join(path, "nodes.json"), [dict({"text": "", "label": "", "rid": ""}, **node)])
            return path
        dark = cell["android/profile-trips/dark/none"]
        with self.assertRaisesRegex(FEError, "another screen"):
            set_status(cov, dark["id"], "done", evidence=[pack(dark, text="Лента")])
        set_status(cov, dark["id"], "done", evidence=[pack(dark, text="Лента")], note="blank, only the tab bar")
        default = cell["android/profile-trips/default/none"]
        set_status(cov, default["id"], "done", evidence=[pack(default, rid="trips-list-0")])
        rapid = cell["android/profile-trips/default/rapid-tap"]  # leaving the screen is what it may reveal
        set_status(cov, rapid["id"], "done", evidence=[pack(rapid, text="Поделиться")])
        self.assertEqual([c["status"] for c in cell.values()], ["done"] * 3)

    def test_resume_blocks_mutating_cells_when_mutation_no_longer_allowed(self):
        cov = plan(MUT_SCREEN, BOTH_OK_MUT_ON, "full")
        ids = [c["id"] for c in next_cells(cov, limit=100, mutating_allowed=False)]
        self.assertFalse(any("edit-delete-restore" in i for i in ids))
        self.assertTrue(any(c["status"] == "blocked" and c["mutating"] for c in cov["cells"]))

    def test_recheck_cells_and_ios_offline_gap(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0]["states"] = ["default", "offline", "loading"]
        cov = plan(cfg, BOTH_OK_MUT_OFF, "quick", ["profile-trips"], recheck=["BUG-12"])
        ids = [c["id"] for c in cov["cells"]]
        self.assertIn("android/recheck-BUG-12/default/replay", ids)
        self.assertIn("ios/recheck-BUG-12/default/replay", ids)
        whats = " ".join(u["what"] + " " + u["why"] for u in cov["unchecked"])
        self.assertIn("ios/profile-trips/offline", whats)
        self.assertIn("hooks.loading", whats)

    def test_seed_states_need_mutation_too(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["hooks"] = {"seed": "npm run seed"}
        cfg["screens"][0]["states"] = ["empty"]
        self.assertFalse(any(c["state"] == "empty" for c in plan(cfg, BOTH_OK_MUT_OFF, "full")["cells"]))
        self.assertTrue(any(c["state"] == "empty" for c in plan(cfg, BOTH_OK_MUT_ON, "full")["cells"]))

    def test_next_cells_skips_done_for_resume(self):
        cov = plan(ONE_SCREEN, BOTH_OK_MUT_OFF, "full")
        first = cov["cells"][0]["id"]
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        set_status(cov, first, "done", evidence=[make_pack(root, cov["cells"][0])])
        nxt = next_cells(cov, limit=2)
        self.assertEqual(len(nxt), 2)
        self.assertNotIn(first, [c["id"] for c in nxt])
        self.assertEqual(nxt[0]["id"], cov["cells"][1]["id"])


class ConcurrentSetTest(unittest.TestCase):
    def test_parallel_navigators_lose_no_update(self):
        import json
        import sys
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0]["states"] = ["default", "dark", "font-xl", "keyboard"]
        cfg["screens"][0]["probes"] = ["rapid-tap", "tap-outside", "swipe-inside", "tap-everything"]
        cov = plan(cfg, BOTH_OK_MUT_OFF, "full")
        path = os.path.join(root, "coverage.json")
        write_json(path, cov)
        script = os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "fresh-eyes", "skills",
                              "fresh-eyes", "scripts", "coverage.py")
        procs = [subprocess.Popen([sys.executable, script, "set", "--cov", path, "--cell", c["id"], "--status",
                                   "done", "--evidence", make_pack(root, c)], stdout=subprocess.DEVNULL)
                 for c in cov["cells"]]
        for p in procs:
            p.wait()
        with open(path, encoding="utf-8") as f:
            statuses = [c["status"] for c in json.load(f)["cells"]]
        self.assertEqual(statuses, ["done"] * len(statuses))


class VerdictTest(unittest.TestCase):
    def setUp(self):
        self.cov = plan(ONE_SCREEN, BOTH_OK_MUT_OFF, "full")
        self.cov["unchecked"] = []
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)

    def test_verdict_clean_only_when_full(self):
        packs = done_all(self.cov, self.root)
        self.assertEqual(verdict(self.cov, [], "ru", packs)["code"], "CLEAN")
        self.assertEqual(verdict(self.cov, [], "ru", packs)["label"], "ЧИСТО")
        set_status(self.cov, self.cov["cells"][0]["id"], "todo")
        self.assertEqual(verdict(self.cov, [], "ru", packs)["code"], "INCOMPLETE")

    def test_a_build_swapped_mid_run_is_never_clean(self):
        packs = done_all(self.cov, self.root)
        v = verdict(self.cov, [], "en", packs, ["android emulator-5560: aaaa -> bbbb"])
        self.assertEqual(v["code"], "INCOMPLETE")
        self.assertIn("aaaa -> bbbb", " ".join(v["reasons"]))

    def test_duplicates_count_once_and_only_onto_an_equal_finding(self):
        packs = done_all(self.cov, self.root)

        def f(fid, sev, verdict_, file="android/a/findings.json", **kw):
            return dict(id=fid, kind="BUG", severity=sev, verdict=verdict_, title=fid, _file=file, **kw)
        dup = f("f2", "high", "confirmed", same_as="android/a/findings.json#f1")
        v = verdict(self.cov, [f("f1", "high", "confirmed"), dup], "en", packs)
        self.assertIn("1 confirmed high: f1", v["reasons"])
        self.assertIn("1 duplicate findings merged", v["reasons"])
        for target in (f("f1", "high", "rejected"), f("f1", "low", "confirmed"), f("f1", "high", "confirmed",
                                                                                       file="android/b/findings.json")):
            reasons = " | ".join(verdict(self.cov, [target, dup], "en", packs)["reasons"])
            self.assertIn("f2", reasons)  # the duplicate counts on its own
            self.assertNotIn("merged", reasons)
        self.assertEqual(v["merged"], ["android/a/findings.json#f2"])
        pending_dup = f("f2", "low", "pending", same_as="android/a/findings.json#f1")
        self.assertEqual(verdict(self.cov, [f("f1", "low", "confirmed"), pending_dup], "en", packs)["code"],
                         "INCOMPLETE")
        a = f("f1", "high", "confirmed", same_as="android/a/findings.json#f2")
        b = f("f2", "high", "confirmed", same_as="android/a/findings.json#f1")
        c = f("f3", "high", "confirmed", same_as="android/a/findings.json#f1")
        for group in ([a, b], [a, b, c], [dup]):  # each other, a chain, a target not passed
            self.assertEqual(verdict(self.cov, group, "en", packs)["merged"], [])

    def test_load_findings_tags_files_relative_to_the_run(self):
        os.makedirs(os.path.join(self.root, "android", "a"))
        path = os.path.join(self.root, "android", "a", "findings-2.json")
        with open(path, "w") as fh:
            json.dump({"findings": [{"id": "f1"}], "reviewed_packs": []}, fh)
        findings, _ = _load_findings([path], self.root)
        self.assertEqual(findings[0]["_file"], os.path.join("android", "a", "findings-2.json"))

    def test_unreviewed_evidence_blocks_clean(self):
        packs = done_all(self.cov, self.root)
        self.assertEqual(verdict(self.cov, [], "en")["code"], "INCOMPLETE")  # no reviewer at all
        v = verdict(self.cov, [], "en", packs[1:])
        self.assertEqual(v["code"], "INCOMPLETE")
        self.assertIn("no reviewer looked at", " ".join(v["reasons"]))
        relative = [os.path.relpath(p) for p in packs]  # the reviewer may list paths in another form
        self.assertEqual(verdict(self.cov, [], "en", relative)["code"], "CLEAN")

    def test_na_empty_plan_and_platformless_paths_never_clean(self):
        packs = done_all(self.cov, self.root)
        with self.assertRaises(FEError):
            set_status(self.cov, self.cov["cells"][0]["id"], "na")  # needs a reason
        set_status(self.cov, self.cov["cells"][0]["id"], "na", note="no input on this screen")
        v = verdict(self.cov, [], "en", packs)
        self.assertEqual(v["code"], "INCOMPLETE")
        self.assertIn("not applicable", " ".join(v["reasons"]))
        self.assertEqual(verdict({"cells": [], "unchecked": []}, [], "en")["code"], "INCOMPLETE")
        loose = os.path.join(self.root, "feed", "default-none")
        write_json(os.path.join(loose, "meta.json"), {})
        with self.assertRaises(FEError):
            set_status(self.cov, self.cov["cells"][1]["id"], "done", evidence=[loose])

    def test_pending_findings_block_clean(self):
        packs = done_all(self.cov, self.root)
        v = verdict(self.cov, [finding("high", "pending")], "en", packs)
        self.assertEqual(v["code"], "INCOMPLETE")
        self.assertIn("not verified", " ".join(v["reasons"]))

    def test_unchecked_items_block_clean(self):
        packs = done_all(self.cov, self.root)
        self.cov["unchecked"] = [{"what": "ios", "why": "no device"}]
        self.assertEqual(verdict(self.cov, [], "en", packs)["code"], "INCOMPLETE")

    def test_verdict_high_blocks_even_incomplete(self):
        v = verdict(self.cov, [finding("high")], "ru")
        self.assertEqual(v["code"], "BLOCK")
        self.assertEqual(v["label"], "НЕ ВЫПУСКАТЬ")

    def test_medium_full_is_issues(self):
        packs = done_all(self.cov, self.root)
        self.assertEqual(verdict(self.cov, [finding("medium")], "en", packs)["code"], "ISSUES")

    def test_verdict_low_rejected_and_imp_ignored(self):
        packs = done_all(self.cov, self.root)
        fs = [finding("low"), finding("low", "unsure"), finding("high", "rejected"), finding("high", kind="IMP")]
        self.assertEqual(verdict(self.cov, fs, "en", packs)["code"], "CLEAN")

    def test_unsure_high_or_medium_keeps_clean_away(self):
        packs = done_all(self.cov, self.root)
        for sev in ("high", "medium"):
            v = verdict(self.cov, [finding(sev, "unsure")], "en", packs)
            self.assertEqual(v["code"], "INCOMPLETE", sev)
            self.assertIn("need a human look", " ".join(v["reasons"]))

    def test_map_has_symbols(self):
        set_status(self.cov, self.cov["cells"][0]["id"], "done", evidence=[make_pack(self.root, self.cov["cells"][0])])
        set_status(self.cov, self.cov["cells"][1]["id"], "failed", note="crashed")
        m = coverage_map(self.cov, "en")
        self.assertIn("profile-trips", m)
        self.assertIn("✓", m)
        self.assertIn("✗", m)
        self.assertIn("?", m)


class PruneTest(unittest.TestCase):
    def test_old_runs_lose_bulky_files_unless_a_finding_links_them(self):
        with tmpdir() as d:
            def run(name, linked=False):
                pack = os.path.join(d, name, "android", "feed", "default-none")
                os.makedirs(pack)
                for f in ("screen.png", "video.mp4", "screen_1000.png"):
                    with open(os.path.join(pack, f), "wb") as fh:
                        fh.write(b"x" * 10)
                if linked:
                    write_json(os.path.join(d, name, "android", "feed", "findings.json"),
                               {"findings": [{"evidence": ["android/feed/default-none/screen.png"]}]})
                return pack
            packs = [run("20261020-1000-quick", linked=True), run("20261021-1000-quick"),
                     run("20261022-1000-quick"), run("20261023-1000-quick")]
            self.assertEqual(prune(d, keep=2), 30)
            left = [sorted(os.listdir(p)) for p in packs]
        self.assertEqual(left[0], ["screen.png", "screen_1000.png"])
        self.assertEqual(left[1], ["screen_1000.png"])
        self.assertEqual(left[2], ["screen.png", "screen_1000.png", "video.mp4"])
        self.assertEqual(left[3], left[2])


class DiffScreensTest(unittest.TestCase):
    def _git(self, repo, *args):
        subprocess.run(["git", "-C", repo] + list(args), check=True, stdout=subprocess.DEVNULL)

    def _write(self, repo, rel, text):
        p = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def test_screens_from_diff_follows_imports(self):
        with tmpdir() as repo:
            self._git(repo, "init", "-q")
            self._write(repo, "apps/mobile/app/(tabs)/profile.tsx",
                        "import ProfilePager from '../../components/ProfilePager';\n")
            self._write(repo, "apps/mobile/components/ProfilePager.tsx",
                        "import { TripGrid } from '@/components/TripGrid';\n")
            self._write(repo, "apps/mobile/components/TripGrid.tsx", "export const TripGrid = 1;\n")
            self._write(repo, "apps/mobile/components/Unused.tsx", "export const U = 1;\n")
            self._git(repo, "add", "-A")
            self._git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
            self._write(repo, "apps/mobile/components/TripGrid.tsx", "export const TripGrid = 2;\n")
            self._write(repo, "apps/mobile/components/Unused.tsx", "export const U = 2;\n")
            out = screens_from_diff(repo, "HEAD", ONE_SCREEN)
            self.assertEqual(out["screens"], ["profile-trips"])
            self.assertEqual(out["unmapped"], ["apps/mobile/components/Unused.tsx"])

    def test_a_screens_own_file_outranks_shared_components(self):
        with tmpdir() as repo:
            self._git(repo, "init", "-q")
            ids = ["a", "b", "c", "d", "e"]
            for sid in ids:
                self._write(repo, "app/%s.tsx" % sid, "import A from '../components/A';\nimport B from '../components/B';\n")
            self._write(repo, "app/settings.tsx", "export default 1;\n")
            for name in ("A", "B"):
                self._write(repo, "components/%s.tsx" % name, "export default 1;\n")
            self._git(repo, "add", "-A")
            self._git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
            for rel in ("components/A.tsx", "components/B.tsx", "app/settings.tsx"):
                self._write(repo, rel, "export default 2;\n")
            cfg = {"app": {"expo_root": "."},
                   "screens": [{"id": sid, "route": "app/%s.tsx" % sid} for sid in ids + ["settings"]]}
            self.assertEqual(screens_from_diff(repo, "HEAD", cfg)["screens"][0], "settings")

    def test_plan_dedupes_and_checks_the_limit(self):
        cov = plan(ONE_SCREEN, BOTH_OK_MUT_OFF, "full", ["profile-trips", "profile-trips"], recheck=["BUG-1", "BUG-1"])
        ids = [c["id"] for c in cov["cells"]]
        self.assertEqual(len(ids), len(set(ids)))
        with self.assertRaises(FEError):
            plan(ONE_SCREEN, BOTH_OK_MUT_OFF, "quick", ["profile-trips"], max_screens=0)

    def test_bad_base_is_an_error_not_an_empty_diff(self):
        with tmpdir() as repo:
            self._git(repo, "init", "-q")
            self._write(repo, "app/index.tsx", "export default 1;\n")
            self._git(repo, "add", "-A")
            self._git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
            with self.assertRaises(FEError):
                screens_from_diff(repo, "no-such-ref^", ONE_SCREEN)

    def test_unmapped_with_expo_root_dot(self):
        with tmpdir() as repo:
            self._git(repo, "init", "-q")
            self._write(repo, "app/index.tsx", "export default 1;\n")
            self._write(repo, "components/Lonely.tsx", "export const L = 1;\n")
            self._git(repo, "add", "-A")
            self._git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
            self._write(repo, "components/Lonely.tsx", "export const L = 2;\n")
            cfg = dict(ONE_SCREEN, app={"expo_root": "."})
            self.assertEqual(screens_from_diff(repo, "HEAD", cfg)["unmapped"], ["components/Lonely.tsx"])


if __name__ == "__main__":
    unittest.main()
