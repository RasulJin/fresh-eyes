import os
import unittest

from tests.fresh_eyes.helpers import tmpdir
from bench import candidates, false_clean, load_run_findings, score, sheet, summary
from fe_common import FEError, write_json

CASE = {"id": "BUG-357", "screen": "side-menu", "platform": "android", "kind": "bug", "group": "live",
        "expect": {"description": "the «Import from text or a link» item is cut with an ellipsis", "bbox": None}}


def f(**kw):
    base = {"id": "f1", "kind": "BUG", "severity": "medium", "screen": "side-menu", "platform": "android",
            "title": "", "actual": "", "verdict": "confirmed", "bbox": [0, 0, 10, 10], "evidence": ["a/som.png"]}
    base.update(kw)
    return base


def run_with(root, name, findings, screen="side-menu"):
    run = os.path.join(root, name)
    write_json(os.path.join(run, "android", screen, "findings.json"), {"findings": findings})
    return run


class CandidatesTest(unittest.TestCase):
    def test_screen_platform_and_verdict_decide_not_words(self):
        self.assertEqual(len(candidates(CASE, [f(title="anything at all")])), 1)
        self.assertEqual(candidates(CASE, [f(screen="feed"), f(platform="ios"), f(verdict="rejected")]), [])
        self.assertEqual(len(candidates(CASE, [f(verdict="unsure")])), 1)

    def test_bbox_gate_and_also_screens(self):
        case = dict(CASE, expect=dict(CASE["expect"], bbox=[100, 100, 100, 100]))
        self.assertEqual(len(candidates(case, [f(bbox=[120, 120, 100, 100])])), 1)
        self.assertEqual(candidates(case, [f(bbox=[500, 500, 50, 50])]), [])
        self.assertEqual(len(candidates(dict(CASE, also_screens=["profile"]), [f(screen="profile")])), 1)

    def test_journey_findings_count_for_their_real_screen(self):
        with tmpdir() as d:
            run_with(d, "r", [f(screen="side-menu")], screen="journey")
            got = load_run_findings(os.path.join(d, "r"), [CASE])["BUG-357"]
        self.assertEqual(got[0]["ref"], os.path.join("android", "journey", "findings.json") + "#f1")

    def test_a_run_id_or_an_unreviewed_run_is_refused(self):
        with tmpdir() as d:
            os.makedirs(os.path.join(d, "unreviewed", "android"))
            for run in ("20261028-2124-quick", os.path.join(d, "unreviewed")):
                with self.assertRaises(FEError):
                    load_run_findings(run, [CASE])


class SheetScoreTest(unittest.TestCase):
    def build(self, d):
        runs = [run_with(d, "a%d" % i, [f(title="hit")] if i < 2 else []) for i in range(3)]
        fix = run_with(d, "b", [f(title="still flagged")])
        return sheet([CASE], runs, {"BUG-357": fix})

    def test_roles_stay_out_of_the_judges_sheet(self):
        with tmpdir() as d:
            data, roles = self.build(d)
        self.assertEqual(len(data["entries"]), 4)
        self.assertNotIn("role", str(data["entries"]))
        self.assertEqual(sorted(r["role"] for r in roles.values()), ["attempt"] * 3 + ["fix"])
        self.assertIn("ellipsis", data["cases"]["BUG-357"])

    def test_unjudged_sheet_refuses_to_score(self):
        with tmpdir() as d:
            data, roles = self.build(d)
        with self.assertRaises(FEError):
            score(data, roles)

    def test_two_of_three_and_a_match_on_the_fix_build_is_a_false_positive(self):
        with tmpdir() as d:
            data, roles = self.build(d)
        for e in data["entries"]:
            e["match"] = e["candidates"][0]["ref"] if e["candidates"] else False
        scores, fps = score(data, roles)
        self.assertEqual((scores["BUG-357"]["hits"], scores["BUG-357"]["caught"]), (2, True))
        self.assertEqual((scores["BUG-357"]["fix_flagged"], fps["pairs"]), ("1/1", 1))
        data["entries"][1]["match"] = False  # a near miss judged as another defect
        self.assertFalse(score(data, roles)[0]["BUG-357"]["caught"])

    def test_summary_counts(self):
        scores = {"BUG-357": {"caught": True}, "OCT-1": {"caught": False}}
        s = summary([CASE, dict(CASE, id="OCT-1", group="october")], scores, {"pairs": 0}, confirmed=4, rejected=1)
        self.assertEqual((s["live"], s["october"], s["rejected_share"]), ("1/1", "0/1", 0.2))


class FalseCleanTest(unittest.TestCase):
    def test_clean_run_that_missed_a_known_bug_is_counted(self):
        with tmpdir() as run:
            pack = os.path.join(run, "android", "side-menu", "default-none")
            write_json(os.path.join(pack, "meta.json"), {})
            write_json(os.path.join(run, "coverage.json"), {"cells": [
                {"id": "android/side-menu/default/none", "platform": "android", "screen": "side-menu",
                 "status": "done", "evidence": [pack]}], "unchecked": []})
            write_json(os.path.join(run, "android", "side-menu", "findings.json"),
                       {"reviewed_packs": [pack], "findings": []})
            roles = {"e1": {"case": "BUG-357", "run": run, "role": "attempt"}}
            judged = {"entries": [{"entry": "e1", "match": False}]}
            self.assertEqual([b["missed"] for b in false_clean(judged, roles, [CASE])], [["BUG-357"]])
            judged["entries"][0]["match"] = "android/side-menu/findings.json#f1"
            self.assertEqual(false_clean(judged, roles, [CASE]), [])
            other = dict(CASE, id="BUG-9", screen="feed")
            roles["e1"]["case"], judged["entries"][0]["match"] = "BUG-9", False
            self.assertEqual(false_clean(judged, roles, [other]), [])  # feed was not in this run: not a miss


if __name__ == "__main__":
    unittest.main()
