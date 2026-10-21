"""Agents follow the docs literally: every `<script>.py <sub> --flag` written in the skill, agents, references
and README must exist in that script's CLI."""
import glob
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "fresh-eyes")
SCRIPTS = os.path.join(ROOT, "skills", "fresh-eyes", "scripts")
DOCS = glob.glob(os.path.join(ROOT, "**", "*.md"), recursive=True)
SPAN = re.compile(r"`([^`]+)`", re.S)
CALL = re.compile(r"\b([a-z_]+)\.py\b")


def _help(*args):
    out = subprocess.run([sys.executable, os.path.join(SCRIPTS, args[0] + ".py")] + list(args[1:]) + ["--help"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    return out.stdout.decode("utf-8", "replace")


class CliContractTest(unittest.TestCase):
    def test_documented_flags_exist(self):
        cache, problems, checked = {}, [], 0
        for doc in DOCS:
            with open(doc, encoding="utf-8") as f:
                text = f.read()
            for span in SPAN.findall(text):
                calls = list(CALL.finditer(span))
                for i, m in enumerate(calls):
                    script = m.group(1)
                    if not os.path.exists(os.path.join(SCRIPTS, script + ".py")):
                        continue
                    segment = span[m.end():calls[i + 1].start() if i + 1 < len(calls) else len(span)]
                    segment = segment.split(" -- ")[0]  # capture.py around … -- <wrapped command>
                    if script not in cache:
                        cache[script] = _help(script)
                    top = cache[script]
                    subs = re.search(r"\{([\w,-]+)\}", top)
                    words = segment.split()
                    sub = words[0] if words and subs and words[0] in subs.group(1).split(",") else None
                    key = (script, sub)
                    if sub and key not in cache:
                        cache[key] = _help(script, sub)
                    usage = cache[key] if sub else top
                    for flag in re.findall(r"--[a-z][\w-]*", segment):
                        checked += 1
                        if flag not in usage:
                            problems.append("%s: %s.py %s %s" % (os.path.relpath(doc, ROOT), script, sub or "",
                                                                 flag))
        self.assertGreater(checked, 50)
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
