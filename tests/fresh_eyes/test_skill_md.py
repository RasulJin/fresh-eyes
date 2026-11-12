import os
import re
import unittest

from tests.fresh_eyes.helpers import SCRIPTS

SKILL_DIR = os.path.dirname(SCRIPTS)


def text():
    with open(os.path.join(SKILL_DIR, "SKILL.md"), encoding="utf-8") as f:
        return f.read()


class SkillMdTest(unittest.TestCase):
    def test_line_budget(self):
        self.assertLessEqual(len(text().splitlines()), 300)

    def test_frontmatter(self):
        head = text().split("---")[1]
        self.assertIn("name: fresh-eyes", head)
        self.assertNotIn("disable-model-invocation", head)  # "run fresh-eyes" in words must work (0.5.7)
        desc = re.search(r"^description: (.+)$", head, flags=re.M).group(1)
        self.assertIn("only when the user asks", desc)  # what keeps Claude from starting a paid run itself
        self.assertLessEqual(len(desc), 200)

    def test_hard_rules_on_top(self):
        top = "\n".join(text().splitlines()[:40])
        for word in ("CLEAN", "staging", "fresh-eyes", "Maestro CLI"):
            self.assertIn(word, top, word)

    def test_subcommands_present(self):
        body = text()
        for sub in ("## init", "## quick", "## full", "## bench"):
            self.assertIn(sub, body, sub)

    def test_referenced_files_exist(self):
        body = text()
        for rel in re.findall(r"\$(?:S|R)/([\w.-]+\.(?:py|md))", body):
            sub = "scripts" if rel.endswith(".py") else "references"
            self.assertTrue(os.path.exists(os.path.join(SKILL_DIR, sub, rel)), rel)

    def test_agents_called_by_full_name_without_model(self):
        body = text()
        for agent in ("fresh-eyes:navigator", "fresh-eyes:reviewer", "fresh-eyes:verifier"):
            self.assertIn(agent, body)
        self.assertIn("never pass `model`", body)

    def test_run_steps_0_5_9(self):
        # spec slices, backlog index for verifiers, second pass on the same verifier, crash log on every snap
        body = text()
        for word in ("spec.py slice", "spec.py stale", "backlog.py index", "SendMessage", "--package"):
            self.assertIn(word, body, word)


if __name__ == "__main__":
    unittest.main()
