import os
import re
import unittest

from tests.fresh_eyes.helpers import SCRIPTS

PLUGIN = os.path.normpath(os.path.join(SCRIPTS, "..", "..", ".."))
AGENTS = os.path.join(PLUGIN, "agents")


def frontmatter(name):
    with open(os.path.join(AGENTS, name + ".md"), encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, flags=re.S)
    assert m, name
    fields = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        fields[k.strip()] = v.strip()
    return fields, m.group(2)


class AgentsTest(unittest.TestCase):
    def test_descriptions_short_and_internal(self):
        for name in ("navigator", "reviewer", "verifier"):
            fm, _ = frontmatter(name)
            self.assertEqual(fm["name"], name)
            self.assertLessEqual(len(fm["description"]), 150, name)
            self.assertTrue(fm["description"].startswith("Internal step of /fresh-eyes"), name)
            self.assertNotIn("proactive", fm["description"].lower())

    def test_reviewer_and_verifier_cannot_act(self):
        for name in ("reviewer", "verifier"):
            fm, _ = frontmatter(name)
            self.assertEqual(fm["tools"], "Read, Glob, Grep, Write", name)

    def test_navigator_has_an_allowlist(self):
        # an allowlist keeps MCP tool names and the skill list out of the agent's base context (7k vs 30-78k)
        self.assertEqual(frontmatter("navigator")[0]["tools"], "Bash, Read, Glob, Grep, Write")

    def test_models(self):
        self.assertEqual(frontmatter("navigator")[0]["model"], "sonnet")
        self.assertEqual(frontmatter("reviewer")[0]["model"], "opus")
        self.assertEqual(frontmatter("verifier")[0]["model"], "opus")

    def test_backlog_dedup_and_image_budget(self):
        self.assertIn("backlog_id", frontmatter("verifier")[1])
        reviewer = " ".join(frontmatter("reviewer")[1].split())
        self.assertNotIn("every pack is mandatory", reviewer)  # som.png only for the first pack of a state (0.5.9)
        self.assertIn("app_running: false", reviewer)

    def test_reviewer_and_verifier_read_a_spec_slice(self):
        for name in ("reviewer", "verifier"):
            body = frontmatter(name)[1]
            self.assertNotIn(".fresh-eyes/spec.md", body, name)
            self.assertIn("spec/<screen>.md", body, name)

    def test_bodies_reference_existing_files(self):
        root = os.path.join(PLUGIN, "skills", "fresh-eyes")
        for name in ("navigator", "reviewer", "verifier"):
            _, body = frontmatter(name)
            for rel in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/skills/fresh-eyes/([\w./-]+)", body):
                self.assertTrue(os.path.exists(os.path.join(root, rel)), (name, rel))


if __name__ == "__main__":
    unittest.main()
