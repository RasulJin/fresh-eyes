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
        self.assertEqual(documented, set(PROBES))

    def test_every_state_documented(self):
        documented = set(re.findall(r"^### ([a-z-]+)\s*$", read("checklist.md"), flags=re.M))
        self.assertTrue(set(STATES) <= documented, set(STATES) - documented)

    def test_reference_line_budget(self):
        for name in FILES:
            self.assertLessEqual(len(read(name).splitlines()), 200, name)

    def test_no_project_specific_data(self):
        for name in FILES:
            text = read(name).lower()
            for word in ("voiova", "vio ", "emulator-5554"):
                self.assertNotIn(word, text, (name, word))


if __name__ == "__main__":
    unittest.main()
