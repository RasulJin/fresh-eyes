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

    def test_p37_a_dark_cell_puts_the_theme_back_and_proves_it(self):
        # 0.5.13 run: after dark-none the app stayed on its Dark theme, three light import cells were shot dark
        nav = agent("navigator")
        for word in ("capture.py snap --out <pack>/restored", "`lit_top` in its `meta.json` under 60",
                     "`--app-theme` names only an option you picked in this cell", "`app_theme_set`"):
            self.assertIn(word, nav, word)
        self.assertNotIn("the app's option now picked", nav)
        self.assertIn("`restored/` only proves the light theme came back", agent("reviewer"))

    def test_p38_the_probe_runs_on_its_screen_and_no_evidence_is_deleted(self):
        # 0.5.13 quick: step-10 and step-13 deleted to pass the order check; ten "no change" taps under a photo
        # viewer; keyboard-open "return" snapped without ever leaving
        nav = agent("navigator")
        for word in ("never copy, move, delete or write a pack's files or folders yourself",
                     "capture.py step --renumber <pack>", '"not this screen: <marker> missing"'):
            self.assertIn(word, nav, word)
        probes = ref("probes.md")
        for word in ("snap where you landed into `<pack>/left` (the screen's marker gone)",
                     'Three `"changed": false` in a row', "`<pack>/single`"):
            self.assertIn(word, probes, word)
        self.assertIn("`left/` only that a probe left the screen", agent("reviewer"))

    def test_p40_fewer_requests_that_carry_their_conditions(self):
        # 0.5.13 run: 45 requests took 1.3 h; repeats ran from another page and at normal animation speed (M3, M9)
        reviewer = agent("reviewer")
        for word in ("ask only when the answer can make a BUG of medium or higher or settles an invariant",
                     "at most 2 per group of packs"):
            self.assertIn(word, reviewer, word)
        self.assertIn("at most 2 per group", ref("checklist.md"))
        conditions = '"conditions": {"slow_animations"'
        self.assertIn(conditions, agent("verifier"))
        self.assertIn(conditions, ref("report-format.md"))
        nav = agent("navigator")
        self.assertIn("reach the pack's screen first and set its `conditions`", nav)
        self.assertIn("check on `nav.py screen` that it starts as the original did", nav)
        body = skill()
        self.assertIn("findings.py requests --run $RUN", body)
        self.assertIn("≤15 open entries per call", body)

    def test_p41_two_navigators_one_per_queue(self):
        # 0.5.13 run: navigation took 5.3 h on one device while the small one idled (M9)
        body = skill()
        for word in ("two `fresh-eyes:navigator`s at once, one per queue", "--queue main|small",
                     "`small` on its `small_device_id`", "in one blocking call each, in one message"):
            self.assertIn(word, body, word)
        self.assertIn("these cells come with the small device as yours", agent("navigator"))

    def test_p42_checkpoints_resume_and_short_replies(self):
        # 0.5.13 run: the orchestrator read 87 M (M10); a hand-written RESUME.md carried the run across a pause
        body = skill()
        for word in ("resume [<run>]", "## Checkpoints and resume", "`$RUN/RESUME.md`",
                     "`/fresh-eyes resume <run>` in a new session", "coverage.py notes --run $RUN",
                     "their details go to `<run>/notes/`", "in two messages: one Bash call chaining"):
            self.assertIn(word, body, word)
        self.assertNotIn("[--resume]", body)
        for name in ("navigator", "reviewer", "verifier"):
            self.assertIn("<run>/notes/<screen>-", agent(name), name)

    def test_p43_agents_read_in_batches(self):
        # 0.5.13 run: reviewers and verifiers grew from 12 k to 100 k over 13-14 turns, one file per turn (M10)
        for name in ("reviewer", "verifier"):
            text = agent(name)
            for word in ("read in batches", "with one Glob", "never open a file twice"):
                self.assertIn(word, text, (name, word))
        nav = agent("navigator")
        for word in ("both in one message", "in one Bash call (chained with `&&`)",
                     "`taps.py all --pack <pack> --cell <cell>`"):
            self.assertIn(word, nav, word)
        self.assertIn("`taps.py all --pack <pack> --cell <cell>` taps every clickable node", ref("probes.md"))

    def test_p45_open_tasks_outside_the_backlog_reach_the_index(self):
        # 0.5.13 run: thread#1f1 repeated an open board task (KDD #547) and reached the report without its id (M4)
        config = ref("config-format.md")
        for word in ('"extra": {"cmd":', "`extra` (optional)", "each line `<id>\\t<title>`"):
            self.assertIn(word, config, word)
        report = ref("report-format.md")
        self.assertIn("or an id of config `backlog.extra`", report)
        self.assertIn("then the `backlog.extra` lines", report)
        self.assertIn("an id from `backlog.extra` is only named in the report", skill())

    def test_p48_nothing_to_scroll_is_na(self):
        # 0.5.13 run: flight-search/default/swipe-inside blocked, so the verdict listed it as not done
        for text in (ref("probes.md"), agent("navigator")):
            self.assertIn('`--status na` with note "not enough items"', text)


if __name__ == "__main__":
    unittest.main()
