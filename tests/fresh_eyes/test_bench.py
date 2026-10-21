import os
import unittest

from tests.fresh_eyes.helpers import tmpdir
from bench import false_clean, false_positives, load_run_findings, match, score, summary
from fe_common import write_json

CASE = {"id": "BUG-357", "screen": "side-menu", "platform": "android", "ref": "main", "kind": "bug", "pair": None,
        "group": "live", "expect": {"checks": ["truncation"], "keywords": ["обрез", "сс…"], "bbox": None}}


def f(**kw):
    base = {"kind": "BUG", "severity": "medium", "screen": "side-menu", "platform": "android", "check": "taste",
            "title": "", "actual": "", "verdict": "confirmed", "bbox": [0, 0, 10, 10]}
    base.update(kw)
    return base


class MatchTest(unittest.TestCase):
    def test_keyword_casefold_cyrillic(self):
        self.assertTrue(match(CASE, [f(title="Пункт «Импорт…» ОБРЕЗАН многоточием")]))

    def test_check_name_matches(self):
        self.assertTrue(match(CASE, [f(check="truncation")]))

    def test_other_screen_or_platform_does_not_match(self):
        self.assertFalse(match(CASE, [f(check="truncation", screen="feed")]))
        self.assertFalse(match(CASE, [f(check="truncation", platform="ios")]))

    def test_rejected_does_not_count_unsure_does(self):
        self.assertFalse(match(CASE, [f(check="truncation", verdict="rejected")]))
        self.assertTrue(match(CASE, [f(check="truncation", verdict="unsure")]))

    def test_bbox_iou_gate(self):
        case = dict(CASE, expect=dict(CASE["expect"], bbox=[100, 100, 100, 100]))
        self.assertTrue(match(case, [f(check="truncation", bbox=[120, 120, 100, 100])]))
        self.assertFalse(match(case, [f(check="truncation", bbox=[500, 500, 50, 50])]))


class ScoreTest(unittest.TestCase):
    def test_two_of_three_rule(self):
        hit, miss = [f(check="truncation")], []
        self.assertTrue(score([CASE], [{"BUG-357": hit}, {"BUG-357": hit}, {"BUG-357": miss}])["BUG-357"]["caught"])
        self.assertFalse(score([CASE], [{"BUG-357": hit}, {"BUG-357": miss}, {"BUG-357": miss}])["BUG-357"]["caught"])

    def test_single_attempt_needs_one_hit(self):
        self.assertTrue(score([CASE], [{"BUG-357": [f(check="truncation")]}])["BUG-357"]["caught"])

    def test_control_fp(self):
        control = dict(CASE, id="CTRL-1", kind="control")
        fp = false_positives([control], fix_runs={}, control_runs={"CTRL-1": [f(check="overlap")]})
        self.assertEqual(fp["control"], 1)

    def test_pair_fp(self):
        fp = false_positives([CASE], fix_runs={"BUG-357": [f(check="truncation")]}, control_runs={})
        self.assertEqual(fp["pairs"], 1)

    def test_summary_counts(self):
        cases = [CASE, dict(CASE, id="OCT-1", group="october", expect=dict(CASE["expect"], checks=["clipped"],
                                                                           keywords=["zzz"]))]
        scores = score(cases, [{"BUG-357": [f(check="truncation")], "OCT-1": []}])
        s = summary(cases, scores, {"pairs": 0, "control": 0}, confirmed=4, rejected=1)
        self.assertEqual(s["live"], "1/1")
        self.assertEqual(s["october"], "0/1")
        self.assertAlmostEqual(s["rejected_share"], 0.2)


class RunLoadTest(unittest.TestCase):
    def test_journey_findings_count_for_their_real_screen(self):
        with tmpdir() as d:
            write_json(os.path.join(d, "android", "journey", "findings.json"),
                       {"findings": [f(check="truncation", screen="side-menu")]})
            write_json(os.path.join(d, "android", "feed", "findings.json"), {"findings": [f(screen="feed")]})
            got = load_run_findings(d, [CASE])
        self.assertTrue(match(CASE, got["BUG-357"]))

    def test_also_screens(self):
        case = dict(CASE, screen="post-detail", also_screens=["thread"])
        self.assertTrue(match(case, [f(check="truncation", screen="thread")]))


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
            self.assertEqual([b["missed"] for b in false_clean([run], [CASE])], [["BUG-357"]])
            write_json(os.path.join(run, "android", "side-menu", "findings.json"),
                       {"reviewed_packs": [pack], "findings": [f(check="truncation", severity="low")]})
            self.assertEqual(false_clean([run], [CASE]), [])


if __name__ == "__main__":
    unittest.main()
