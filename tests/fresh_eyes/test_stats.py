import json
import os
import shutil
import unittest
from datetime import datetime, timezone
from unittest import mock

from tests.fresh_eyes.helpers import FIXTURES, tmpdir
from fe_common import read_json, write_json
import stats

T0 = 1700000000  # the run's first file


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def _jsonl(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in records)


def _said(t, mid, cache, *tools):
    return {"type": "assistant", "timestamp": _iso(T0 + t), "message": {
        "id": mid, "usage": {"cache_read_input_tokens": cache},
        "content": [{"type": "tool_use", "name": n, "input": {}} for n in tools]}}


class ScoreTest(unittest.TestCase):
    def test_the_card_counts_the_run_folder_and_the_sessions_transcripts_and_keeps_the_judges_fields(self):
        with tmpdir() as d:
            run = os.path.join(d, "run")
            shutil.copytree(os.path.join(FIXTURES, "score-run"), run)
            for name in os.listdir(run):
                os.utime(os.path.join(run, name), (T0, T0))
            os.utime(os.path.join(run, "report.md"), (T0 + 5 * 3600,) * 2)
            projects = os.path.join(d, "projects")
            _jsonl(os.path.join(projects, "-repo", "s1.jsonl"), [
                _said(-100, "m0", 1000),  # before the run: not counted
                _said(10, "m1", 100, "TaskStop"), _said(10, "m1", 100, "Bash"),  # one message in two records
                _said(2000, "m3", 7), _said(5 * 3600 + 100, "m4", 1000)])
            _jsonl(os.path.join(projects, "-repo", "s1", "subagents", "agent-a.jsonl"),
                   [_said(500, "a1", 50), {"type": "user", "timestamp": _iso(T0 + 1000)}])
            with mock.patch.object(stats, "PROJECTS", projects):
                out = stats.score(run, "s1")
                card = read_json(out["out"])
                values = {k: card[k]["value"] for k in stats.LIMITS}
                self.assertEqual(values, {
                    "M1": 3, "M2": 0.333, "M3": 0.333, "M4": None, "M5": 3, "M6": 1, "M7": None, "M8": 32,
                    "M9": {"hours": 5.0, "idle_gaps": 1},  # 10, 500, 1000, then 2000: one gap over 10 min
                    "M10": {"total": 157, "orchestrator": 107}, "M11": None,
                    "M12": {"stops": 1, "consent_blocked": 1}})
                self.assertEqual(card["M6"]["of"], 2)  # welcome is stale: its confirmed spec:G-target turns unsure
                self.assertEqual((out["passed"], out["unknown"]), (3, ["M4", "M7", "M11"]))  # M5, M8, M10
                card["M4"] = {"value": 0.04}  # the judge's entry
                card["M1"] = {"value": 0, "by": "judge"}  # its correction: the 3 steps were SKILL.md's
                write_json(out["out"], card)
                with open(os.path.join(run, "run-log.md"), "w", encoding="utf-8") as f:
                    f.write("# Ход прогона\n1. reach поправлен руками\n- заметка\n")
                card = read_json(stats.score(run)["out"])  # no session: the transcript metrics stay
            self.assertEqual((card["M4"]["value"], card["M4"]["ok"]), (0.04, True))
            self.assertEqual((card["M1"]["value"], card["M1"]["ok"], card["M1"]["by"]), (0, True, "judge"))
            self.assertEqual((card["M10"]["value"], card["M10"]["ok"]), ({"total": 157, "orchestrator": 107}, True))
            self.assertEqual((card["M9"]["value"]["idle_gaps"], card["M12"]["value"]["stops"]), (1, 1))
            os.remove(os.path.join(run, "score.json"))
            card = read_json(stats.score(run)["out"])  # nothing counted before: transcript metrics unknown
            self.assertEqual(card["M1"]["value"], 1)  # run-log.md: 1 numbered step
            for name in ("score.json", "run-log.md"):
                os.remove(os.path.join(run, name))
            with open(os.path.join(run, "report.md"), "w", encoding="utf-8") as f:
                f.write("# Отчёт\n1. not a hand move\n")
            m1 = read_json(stats.score(run)["out"])["M1"]
            self.assertEqual((m1["value"], m1["ok"]), (None, None))  # no run log anywhere: unknown, not a free pass
            self.assertIn("M10", card["unknown"])

    def test_m5_counts_a_screens_questions_one_by_one(self):
        # 0.5.11: one bullet per screen held 3 questions joined by ";", the script said 10 and the judge 16
        text = ("## Вопросы\n- **flight-search**:\n  - Коралловая пометка задумана? (баг / задумано)\n"
                "  - Чужое время у Pegasus? (пометка / кэш)\n  - spec text: flight-search#3 «Откуда» → «Из»\n"
                "- **import**: Серая кнопка задумана? (да / нет)\n  - варианты: да / нет\n"
                "- **sign-up**: spec text: welcome#3 «А» → «Б»\n- **thread**:\n  1. Строка родителя всегда?\n"
                "## Открытые конфликты спеки\n- feed: G-hint\n")
        self.assertEqual(stats.questions(text), 4)  # 2 + 1 (options are no question) + 0 + 1; conflicts none

    def test_m2_leaves_out_a_flaky_reject(self):
        with tmpdir() as d:
            run = os.path.join(d, "run")
            shutil.copytree(os.path.join(FIXTURES, "score-run"), run)
            path = os.path.join(run, "android", "welcome", "findings.json")
            data = read_json(path)
            data["findings"].append({"id": "f8", "kind": "BUG", "severity": "low", "verdict": "rejected",
                                     "verify_note": "flaky: seen in default-none, not reproduced in default-none-recheck"})
            write_json(path, data)
            card = read_json(stats.score(run)["out"])
        self.assertEqual((card["M2"]["value"], card["M2"]["flaky"]), (0.286, 1))  # 2 false of 7 ruled
        self.assertEqual(card["M3"]["value"], 0.286)

    def test_m2_counts_a_ruling_copied_by_same_as_auto_once(self):
        with tmpdir() as d:
            run = os.path.join(d, "run")
            shutil.copytree(os.path.join(FIXTURES, "score-run"), run)
            path = os.path.join(run, "android", "welcome", "findings.json")
            data = read_json(path)
            rejected = next(f for f in data["findings"] if f.get("verdict") == "rejected")
            data["findings"] += [dict(rejected, id="f8", same_as_auto="android/welcome/findings.json#" + rejected["id"]),
                                 dict(rejected, id="f9", verdict="confirmed", same_as_auto="android/feed/findings.json#f1")]
            write_json(path, data)
            card = read_json(stats.score(run)["out"])
        self.assertEqual(card["M2"]["value"], 0.333)  # still 2 false of 6 ruled, as without the copies


if __name__ == "__main__":
    unittest.main()
