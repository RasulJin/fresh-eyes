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

    def test_p36_a_mutating_scenario_writes_finds_and_cleans(self):
        # 0.5.13 run: post-detail/default/scenario was mutating, walked read-only and marked done (M7)
        probes = ref("probes.md")
        for word in ('"wrote": "<what was created or changed>"', '"found_in": "<where it was found>"',
                     '"cleaned": "<what was deleted>"', 'A read-only walk is for `"mutating": false` only'):
            self.assertIn(word, probes, word)
        self.assertNotIn("Otherwise do the read-only part", probes)
        self.assertIn("never a read-only walk marked done", agent("navigator"))

    def test_p48_nothing_to_scroll_is_na(self):
        # 0.5.13 run: flight-search/default/swipe-inside blocked, so the verdict listed it as not done
        for text in (ref("probes.md"), agent("navigator")):
            self.assertIn('`--status na` with note "not enough items"', text)


if __name__ == "__main__":
    unittest.main()
