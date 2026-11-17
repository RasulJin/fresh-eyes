"""0.5.14 part c: the instructions text (SKILL.md, agents, references) of judge.md list B, iteration 3."""
import os
import unittest

from tests.fresh_eyes.helpers import SCRIPTS

SKILL_DIR = os.path.dirname(SCRIPTS)
PLUGIN = os.path.normpath(os.path.join(SKILL_DIR, "..", ".."))


def flat(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return " ".join(f.read().split())


def agent(name):
    return flat(PLUGIN, "agents", name + ".md")


def ref(name):
    return flat(SKILL_DIR, "references", name)


def skill():
    return flat(SKILL_DIR, "SKILL.md")


class Text0514Test(unittest.TestCase):
    def test_p35_password_field_tapped_from_a_fresh_dump(self):
        # 0.5.13 run: a password typed into a plain field came back in clear in nav.py's printed screen
        nav = agent("navigator")
        for word in ("Before each `type`, take a fresh `nav.py screen`", "never an id or point from an earlier dump",
                     "no password field for a password"):
            self.assertIn(word, nav, word)


if __name__ == "__main__":
    unittest.main()
