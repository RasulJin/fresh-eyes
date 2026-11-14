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
                    "M1": 3, "M2": 0.2, "M3": 0.4, "M4": None, "M5": 3, "M6": 1, "M7": None, "M8": 31,
                    "M9": {"hours": 5.0, "idle_gaps": 1},  # 10, 500, 1000, then 2000: one gap over 10 min
                    "M10": {"total": 157, "orchestrator": 107}, "M11": None,
                    "M12": {"stops": 1, "consent_blocked": 1}})
                self.assertEqual(card["M6"]["of"], 2)  # welcome is stale: its confirmed spec:G-target turns unsure
                self.assertEqual((out["passed"], out["unknown"]), (3, ["M4", "M7", "M11"]))  # M5, M8, M10
                card["M4"] = {"value": 0.04}  # the judge's entry
                write_json(out["out"], card)
                with open(os.path.join(run, "run-log.md"), "w", encoding="utf-8") as f:
                    f.write("# Ход прогона\n1. reach поправлен руками\n- заметка\n")
                card = read_json(stats.score(run)["out"])  # no session: transcript metrics unknown
            self.assertEqual((card["M4"]["value"], card["M4"]["ok"]), (0.04, True))
            self.assertEqual(card["M1"]["value"], 1)
            self.assertEqual(card["M10"]["ok"], None)
            self.assertIn("M10", card["unknown"])


if __name__ == "__main__":
    unittest.main()
