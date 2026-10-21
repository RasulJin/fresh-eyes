import copy
import os
import shutil
import subprocess
import tempfile
import unittest

from tests.fresh_eyes.helpers import tmpdir
from fe_common import FEError, write_json
from coverage import PROBES, STATES, coverage_map, next_cells, plan, screens_from_diff, set_status, verdict

ONE_SCREEN = {"language": "ru", "app": {"expo_root": "apps/mobile"}, "hooks": {},
              "screens": [{"id": "profile-trips", "route": "app/(tabs)/profile.tsx", "states": ["default", "dark"],
                           "probes": ["rapid-tap", "edit-delete-restore"], "neighbors": []}]}
BOTH_OK_MUT_OFF = {"platforms": {"android": {"ok": True}, "ios": {"ok": True}}, "mutating_allowed": False}
BOTH_OK_MUT_ON = dict(BOTH_OK_MUT_OFF, mutating_allowed=True)
MUT_SCREEN = copy.deepcopy(ONE_SCREEN)
MUT_SCREEN["screens"][0]["probes"] = ["edit-delete-restore"]


def make_pack(root, cell, **meta):
    path = os.path.join(root, cell["platform"], cell["screen"], "%s-%s" % (cell["state"], cell["probe"]))
    write_json(os.path.join(path, "meta.json"), dict({"platform": cell["platform"]}, **meta))
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

    def test_quick_includes_neighbors(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0]["neighbors"] = ["profile-posts"]
        cfg["screens"].append({"id": "profile-posts", "route": "x", "states": ["default"], "probes": []})
        cfg["screens"].append({"id": "feed", "route": "y", "states": ["default"], "probes": []})
        screens = {c["screen"] for c in plan(cfg, BOTH_OK_MUT_OFF, "quick", ["profile-trips"])["cells"]}
        self.assertEqual(screens, {"profile-trips", "profile-posts"})

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

    def test_resume_blocks_mutating_cells_when_mutation_no_longer_allowed(self):
        cov = plan(MUT_SCREEN, BOTH_OK_MUT_ON, "full")
        ids = [c["id"] for c in next_cells(cov, limit=100, mutating_allowed=False)]
        self.assertFalse(any("edit-delete-restore" in i for i in ids))
        self.assertTrue(any(c["status"] == "blocked" and c["mutating"] for c in cov["cells"]))

    def test_recheck_cells_and_ios_offline_gap(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0]["states"] = ["default", "offline", "loading"]
        cov = plan(cfg, BOTH_OK_MUT_OFF, "quick", recheck=["BUG-12"])
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

    def test_unreviewed_evidence_blocks_clean(self):
        packs = done_all(self.cov, self.root)
        self.assertEqual(verdict(self.cov, [], "en")["code"], "INCOMPLETE")  # no reviewer at all
        v = verdict(self.cov, [], "en", packs[1:])
        self.assertEqual(v["code"], "INCOMPLETE")
        self.assertIn("no reviewer looked at", " ".join(v["reasons"]))
        relative = [os.path.relpath(p) for p in packs]  # the reviewer may list paths in another form
        self.assertEqual(verdict(self.cov, [], "en", relative)["code"], "CLEAN")

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

    def test_verdict_low_unsure_and_imp_ignored(self):
        packs = done_all(self.cov, self.root)
        fs = [finding("low"), finding("high", "unsure"), finding("high", "rejected"), finding("high", kind="IMP")]
        self.assertEqual(verdict(self.cov, fs, "en", packs)["code"], "CLEAN")

    def test_map_has_symbols(self):
        set_status(self.cov, self.cov["cells"][0]["id"], "done", evidence=[make_pack(self.root, self.cov["cells"][0])])
        set_status(self.cov, self.cov["cells"][1]["id"], "failed")
        m = coverage_map(self.cov, "en")
        self.assertIn("profile-trips", m)
        self.assertIn("✓", m)
        self.assertIn("✗", m)
        self.assertIn("?", m)


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


if __name__ == "__main__":
    unittest.main()
