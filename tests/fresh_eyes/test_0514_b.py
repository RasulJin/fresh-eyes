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


if __name__ == "__main__":
    unittest.main()
