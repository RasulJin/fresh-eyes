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

    def test_verifier_settles_noise_without_a_question(self):
        body = " ".join(frontmatter("verifier")[1].split())
        for word in ("bounds cut at the parent", "spec text: <rule>", '"class": "spec-text"', "known candidate:",
                     '"class": "known"', "set `kind` to `IMP`", "\"Do not flag\" covers it"):
            self.assertIn(word, body, word)
        reviewer = " ".join(frontmatter("reviewer")[1].split())
        self.assertIn("then no BUG: a `spec-text` or `known` entry", reviewer)

    def test_questions_only_when_spec_code_and_backlog_are_silent(self):
        # 0.5.11: 7 of 11 asked findings were settled by the spec, a code comment or an open backlog entry
        body = " ".join(frontmatter("verifier")[1].split())
        for word in ("only when the spec, the code and the backlog are silent", '"spec conflict: <rule id>, open"',
                     '"class": "conflict"', "or a behaviour a question asks about", "from `backlog.py match`",
                     "and no question: the owner put it in the backlog", '"flaky: seen in <pack>',
                     "a medium two", "An entry `from` a findings file"):
            self.assertIn(word, body, word)
        reviewer = " ".join(frontmatter("reviewer")[1].split())
        for word in ("a tap whose result no frame shows", "an entry in the output's `requests`",
                     '{"class": "conflict"'):
            self.assertIn(word, reviewer, word)
        nav = " ".join(frontmatter("navigator")[1].split())
        self.assertIn('An entry with `"times": N`', nav)

    def test_a_guess_is_a_request_or_an_imp_never_a_bug_0_5_13(self):
        # 0.5.12 run: 19 of 32 rejects had confidence <= 0.4 ("needs a frame", "not checked"), one titled «Не проверено: …»
        reviewer = " ".join(frontmatter("reviewer")[1].split())
        for word in ("A guess is not a BUG", '"do": "resnap|crop|repeat-probe"', "confidence above 0.4",
                     "«Не проверено»", "«нужен кадр»"):
            self.assertIn(word, reviewer, word)
        self.assertNotIn("still write the finding with low confidence", reviewer)
        verifier = " ".join(frontmatter("verifier")[1].split())
        self.assertIn("is a guess: request the evidence that decides it", verifier)
        self.assertIn("what a reviewer could not see", verifier)
        with open(os.path.join(PLUGIN, "skills", "fresh-eyes", "references", "checklist.md"), encoding="utf-8") as f:
            checklist = " ".join(f.read().split())
        self.assertIn('no title "Not verified: …"', checklist)
        self.assertNotIn("still write the finding with low confidence", checklist)

    def test_the_verifier_reads_only_the_backlog_index_0_5_13(self):
        # 0.5.12 run: verifiers opened docs/qa-backlog.md of the working folder, another branch's backlog (M4)
        verifier = " ".join(frontmatter("verifier")[1].split())
        self.assertIn("never open or grep the backlog file itself", verifier)
        with open(os.path.join(PLUGIN, "skills", "fresh-eyes", "SKILL.md"), encoding="utf-8") as f:
            self.assertIn("never the backlog file's path", " ".join(f.read().split()))

    def test_an_r_finding_follows_the_repeat_rules_0_5_13(self):
        # 0.5.12 run: profile-trips#1r1 (medium) confirmed on one adb recheck
        verifier = " ".join(frontmatter("verifier")[1].split())
        for word in ("ruled by the same rules as any BUG", "(a low one 1, a medium 2), else `unsure`, \"seen once"):
            self.assertIn(word, verifier, word)

    def test_a_repeat_disproves_only_under_the_same_conditions_0_5_13(self):
        # 0.5.12 run: feed#2f1 (no avatar right after opening) rejected as flaky by a snap 5 s later
        verifier = " ".join(frontmatter("verifier")[1].split())
        for word in ("A repeat disproves only under the original's conditions", "a cold cache",
                     "that is no repeat and no `flaky:`"):
            self.assertIn(word, verifier, word)

    def test_a_tap_result_needs_tap_json_on_its_target_0_5_13(self):
        # 0.5.12 run: the only high (robot opens Google Maps) was a tap at old coordinates that hit the place chip
        verifier = " ".join(frontmatter("verifier")[1].split())
        for word in ("What a tap did", "`tap.json`", "its `point` lies inside `bounds`",
                     "the tap's after pack (`<pack>/tap-<k>`", "that element was never tapped → `rejected`",
                     "above `keyboard_top` when that is set", "rule on the second pass by that recheck's `tap.json`",
                     "a tap's result by its `tap.json`"):
            self.assertIn(word, verifier, word)

    def test_one_case_is_checked_once_and_known_candidates_reach_the_next_run_0_5_13(self):
        # 0.5.12 run: an English demo trip rejected on 4 screens, «Almaty, Казахстан» 3 times, QA141 tiles 3 rulings
        reviewer = " ".join(frontmatter("reviewer")[1].split())
        self.assertIn("read both before you write any finding", reviewer)
        verifier = " ".join(frontmatter("verifier")[1].split())
        for word in ("`same_as_auto` (set by `findings.py same`", "delete `same_as` and `same_as_auto`",
                     '"test data: <what data', "`known-proposed.md`"):
            self.assertIn(word, verifier, word)
        with open(os.path.join(PLUGIN, "skills", "fresh-eyes", "SKILL.md"), encoding="utf-8") as f:
            skill = " ".join(f.read().split())
        for word in ("findings.py same --run $RUN --findings <the same files>", "with a file in its `verify` list",
                     "findings.py same --run $RUN --findings <all findings*.json>",
                     "findings.py known --run $RUN --out .fresh-eyes/known-proposed.md"):
            self.assertIn(word, skill, word)
        self.assertEqual(skill.count(".fresh-eyes/known-proposed.md`,"), 2)  # reviewer and verifier prompts

    def test_a_conflict_pair_goes_to_one_verifier_0_5_13(self):
        # 0.5.12 run: feed#2f1 rejected as flaky, feed-trips#2f1 confirmed with same_as to it
        verifier = " ".join(frontmatter("verifier")[1].split())
        for word in ("A conflict pair", "both are yours to write", "remove the `same_as` between them"):
            self.assertIn(word, verifier, word)
        with open(os.path.join(PLUGIN, "skills", "fresh-eyes", "SKILL.md"), encoding="utf-8") as f:
            skill = " ".join(f.read().split())
        self.assertIn("Code `CONFLICT` (one defect ruled both ways): each pair of its `conflicts` goes to one verifier",
                      skill)

    def test_an_unchanged_step_is_marked_and_a_copy_is_redone_0_5_13(self):
        # 0.5.12 run: vio-guide step-21 retaken after step-22, byte for byte step-27
        verifier = " ".join(frontmatter("verifier")[1].split())
        self.assertIn('marked `"unchanged": true` in `steps.json`', verifier)
        self.assertIn("are a copy, not evidence: no ruling on them, a `redo-cell`", verifier)

    def test_consents_only_from_the_owners_list(self):
        # 0.5.9: "Community rules → Accept" and an AI notice blocked ~10 checks; the owner allows them in config
        nav = " ".join(frontmatter("navigator")[1].split())
        self.assertIn("only when an entry of `consents`", nav)
        self.assertIn('"consent: <the dialog', nav)
        skill_dir = os.path.join(PLUGIN, "skills", "fresh-eyes")
        for rel in ("SKILL.md", os.path.join("references", "config-format.md")):
            with open(os.path.join(skill_dir, rel), encoding="utf-8") as f:
                self.assertIn("`consents`", f.read(), rel)

    def test_navigator_never_builds_a_pack_and_rechecks_on_the_packs_screen(self):
        # 0.5.11: hand-made packs with an old frame, copied steps, a recheck snapped on another screen
        nav = " ".join(frontmatter("navigator")[1].split())
        for word in ("never copy, move or write a pack's files yourself", "`source: maestro`",
                     "reach the pack's screen first", "never mark that entry done"):
            self.assertIn(word, nav, word)

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
