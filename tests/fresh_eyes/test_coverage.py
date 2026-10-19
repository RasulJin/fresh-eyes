import copy
import os
import subprocess
import unittest

from tests.fresh_eyes.helpers import tmpdir
from fe_common import FEError
from coverage import PROBES, STATES, coverage_map, next_cells, plan, screens_from_diff, set_status, verdict

ONE_SCREEN = {"language": "ru", "app": {"expo_root": "apps/mobile"}, "hooks": {},
              "screens": [{"id": "profile-trips", "route": "app/(tabs)/profile.tsx", "states": ["default", "dark"],
                           "probes": ["rapid-tap", "edit-delete-restore"], "neighbors": []}]}
BOTH_OK_MUT_OFF = {"platforms": {"android": {"ok": True}, "ios": {"ok": True}}, "mutating_allowed": False}


def done_all(cov):
    for c in cov["cells"]:
        set_status(cov, c["id"], "done")
    return cov


def finding(sev, verdict_="confirmed", kind="BUG"):
    return {"kind": kind, "severity": sev, "verdict": verdict_, "title": "x"}


class PlanTest(unittest.TestCase):
    def test_registry_shapes(self):
        self.assertTrue(PROBES["edit-delete-restore"]["mutating"])
        self.assertEqual(PROBES["hardware-back"]["platforms"], ["android"])
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

    def test_platform_only_probe_skipped_on_other_platform(self):
        cfg = copy.deepcopy(ONE_SCREEN)
        cfg["screens"][0].update(states=["default"], probes=["hardware-back"])
        ids = [c["id"] for c in plan(cfg, BOTH_OK_MUT_OFF, "full")["cells"]]
        self.assertIn("android/profile-trips/default/hardware-back", ids)
        self.assertNotIn("ios/profile-trips/default/hardware-back", ids)

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

    def test_next_cells_skips_done_for_resume(self):
        cov = plan(ONE_SCREEN, BOTH_OK_MUT_OFF, "full")
        first = cov["cells"][0]["id"]
        set_status(cov, first, "done", evidence=["p/screen.png"])
        nxt = next_cells(cov, limit=2)
        self.assertEqual(len(nxt), 2)
        self.assertNotIn(first, [c["id"] for c in nxt])
        self.assertEqual(nxt[0]["id"], cov["cells"][1]["id"])


class VerdictTest(unittest.TestCase):
    def setUp(self):
        self.cov = plan(ONE_SCREEN, BOTH_OK_MUT_OFF, "full")
        self.cov["unchecked"] = []

    def test_verdict_clean_only_when_full(self):
        done_all(self.cov)
        self.assertEqual(verdict(self.cov, [], "ru")["code"], "CLEAN")
        self.assertEqual(verdict(self.cov, [], "ru")["label"], "ЧИСТО")
        set_status(self.cov, self.cov["cells"][0]["id"], "todo")
        self.assertEqual(verdict(self.cov, [], "ru")["code"], "INCOMPLETE")

    def test_unchecked_items_block_clean(self):
        done_all(self.cov)
        self.cov["unchecked"] = [{"what": "ios", "why": "no device"}]
        self.assertEqual(verdict(self.cov, [], "en")["code"], "INCOMPLETE")

    def test_verdict_high_blocks_even_incomplete(self):
        v = verdict(self.cov, [finding("high")], "ru")
        self.assertEqual(v["code"], "BLOCK")
        self.assertEqual(v["label"], "НЕ ВЫПУСКАТЬ")

    def test_medium_full_is_issues(self):
        done_all(self.cov)
        self.assertEqual(verdict(self.cov, [finding("medium")], "en")["code"], "ISSUES")

    def test_verdict_low_unsure_and_imp_ignored(self):
        done_all(self.cov)
        fs = [finding("low"), finding("high", "unsure"), finding("high", "rejected"), finding("high", kind="IMP")]
        self.assertEqual(verdict(self.cov, fs, "en")["code"], "CLEAN")

    def test_map_has_symbols(self):
        set_status(self.cov, self.cov["cells"][0]["id"], "done")
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
