import os
import unittest

from tests.fresh_eyes.helpers import tmpdir
from bench import false_positives, load_run_findings, match, score, summary
from fe_common import write_json

CASE = {"id": "KDD-357", "screen": "side-menu", "platform": "android", "ref": "main", "kind": "bug", "pair": None,
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
        self.assertTrue(score([CASE], [{"KDD-357": hit}, {"KDD-357": hit}, {"KDD-357": miss}])["KDD-357"]["caught"])
        self.assertFalse(score([CASE], [{"KDD-357": hit}, {"KDD-357": miss}, {"KDD-357": miss}])["KDD-357"]["caught"])

    def test_single_attempt_needs_one_hit(self):
        self.assertTrue(score([CASE], [{"KDD-357": [f(check="truncation")]}])["KDD-357"]["caught"])

    def test_control_fp(self):
        control = dict(CASE, id="CTRL-1", kind="control")
        fp = false_positives([control], fix_runs={}, control_runs={"CTRL-1": [f(check="overlap")]})
        self.assertEqual(fp["control"], 1)

    def test_pair_fp(self):
        fp = false_positives([CASE], fix_runs={"KDD-357": [f(check="truncation")]}, control_runs={})
        self.assertEqual(fp["pairs"], 1)

    def test_summary_counts(self):
        cases = [CASE, dict(CASE, id="OCT-1", group="october", expect=dict(CASE["expect"], checks=["clipped"],
                                                                           keywords=["zzz"]))]
        scores = score(cases, [{"KDD-357": [f(check="truncation")], "OCT-1": []}])
        s = summary(cases, scores, {"pairs": 0, "control": 0}, confirmed=4, rejected=1)
        self.assertEqual(s["live"], "1/1")
        self.assertEqual(s["october"], "0/1")
        self.assertAlmostEqual(s["rejected_share"], 0.2)


class RunLoadTest(unittest.TestCase):
    def test_load_run_findings_by_screen(self):
        with tmpdir() as d:
            write_json(os.path.join(d, "android", "side-menu", "findings.json"), {"findings": [f(check="truncation")]})
            write_json(os.path.join(d, "android", "feed", "findings.json"), {"findings": [f(screen="feed")]})
            got = load_run_findings(d, [CASE])
        self.assertEqual(len(got["KDD-357"]), 1)


if __name__ == "__main__":
    unittest.main()
