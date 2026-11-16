import os
import re
import unittest

from tests.fresh_eyes.helpers import SCRIPTS
from coverage import PROBES, STATES

REFS = os.path.join(os.path.dirname(SCRIPTS), "references")
FILES = ["checklist.md", "probes.md", "platforms.md", "motion.md", "spec-format.md", "config-format.md",
         "report-format.md"]


def read(name):
    with open(os.path.join(REFS, name), encoding="utf-8") as f:
        return f.read()


class ReferencesTest(unittest.TestCase):
    def test_all_files_exist(self):
        for name in FILES:
            self.assertTrue(os.path.exists(os.path.join(REFS, name)), name)

    def test_every_probe_documented(self):
        documented = set(re.findall(r"^### ([a-z-]+)\s*$", read("probes.md"), flags=re.M))
        self.assertEqual(documented, set(PROBES) | {"edge"})  # edge: only on a verifier's request, never planned

    def test_every_state_documented(self):
        documented = set(re.findall(r"^### ([a-z-]+)\s*$", read("checklist.md"), flags=re.M))
        self.assertTrue(set(STATES) <= documented, set(STATES) - documented)

    def test_reference_line_budget(self):
        for name in FILES:
            self.assertLessEqual(len(read(name).splitlines()), 200, name)

    def test_report_is_short_and_has_companions(self):
        # 0.5.9: 693 lines; IMPs, rejects and the run log moved out, unsure by class, questions by screen (0.5.11)
        text = " ".join(read("report-format.md").split())
        for word in ("at most 300 lines", "imp.md", "rejected.md", "run-log.md", "at most 10 lines",
                     "never one block per finding", "one bullet per screen, never one per finding, at most 10"):
            self.assertIn(word, text, word)

    def test_noise_rules_in_do_not_flag(self):
        # the most frequent unsure / "intended" classes of 0.5.9 (KDD #497/#498 rulings)
        section = " ".join(read("checklist.md").split("## Do not flag", 1)[1].split())
        for word in ("negative margin", "`hitSlop` past the parent's edge does not", "string key", "`spec-text`",
                     "calls deliberate", "`known`", "Names from outside the app's copy", "icon shows",
                     "an IMP"):
            self.assertIn(word, section, word)
        schema = " ".join(read("report-format.md").split())
        self.assertIn('"class": "spec-text | known | conflict (optional)"', schema)
        self.assertIn("## Known candidates", schema)

    def test_no_bug_and_no_question_for_what_the_owner_already_has(self):
        # 0.5.11: 8 of 21 false BUGs were taps with no after-frame; feed#f2 asked about a conflict the spec asks
        section = " ".join(read("checklist.md").split("## Do not flag", 1)[1].split())
        for word in ("A tap whose result no frame shows", '`"changed": false`', "a `conflict` entry"):
            self.assertIn(word, section, word)
        schema = " ".join(read("report-format.md").split())
        for word in ("## Open spec conflicts", "counts the sub-items (M5)", "`flaky:`", '"times": 2'):
            self.assertIn(word, schema, word)
        probes = " ".join(read("probes.md").split())
        for word in ("returns 6 s after the first", "`<pack>/single`", "`taps.py tap --som N --after <pack>/tap-<k>`",
                     "dump taken just before the tap", "snaps the result with `tap.json`"):
            self.assertIn(word, probes, word)

    def test_no_project_specific_data(self):
        for name in FILES:
            text = read(name).lower()
            for word in ("voiova", "vio ", "emulator-5554"):
                self.assertNotIn(word, text, (name, word))


if __name__ == "__main__":
    unittest.main()
