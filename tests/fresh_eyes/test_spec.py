import os
import subprocess
import time
import unittest

from tests.fresh_eyes.helpers import tmpdir
from fe_common import FEError
from spec import slice_spec, stale

SPEC = """# Spec — App
Updated: 2026-01-02

Sources: design/.

## Global
- G-radius: cards use radius 16.

## feed — Feed
- must feed#1: posts open inline.

## profile — Profile
- must profile#1: the grid shows three columns.
### Notes
- profile only.

## settings — Settings
- must settings#1: one toggle per row.
"""
CONFIG = {"app": {"expo_root": "."}, "screens": [{"id": "feed", "route": "app/feed.tsx"},
                                                 {"id": "settings", "route": "app/settings.tsx"}]}


class SliceTest(unittest.TestCase):
    def test_keeps_header_global_and_listed_sections_in_spec_order(self):
        out = slice_spec(SPEC, ["settings", "feed", "nope"], {"updated": "2026-01-02", "stale_screens": ["feed"]})
        self.assertEqual(out, """# Spec — App
Updated: 2026-01-02

Sources: design/.

## Global
- G-radius: cards use radius 16.

## feed — Feed
⚠ stale since 2026-01-02
- must feed#1: posts open inline.

## settings — Settings
- must settings#1: one toggle per row.

## nope: no section in spec.md
""")

    def test_without_stale_file_nothing_is_marked(self):
        out = slice_spec(SPEC, ["profile"])
        self.assertIn("### Notes\n- profile only.", out)
        self.assertNotIn("⚠", out)
        self.assertNotIn("## feed", out)


class StaleTest(unittest.TestCase):
    def _git(self, repo, *args, date=None):
        env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date) if date else None
        subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t"] + list(args), check=True,
                       stdout=subprocess.DEVNULL, env=env)

    def _write(self, repo, rel, text):
        p = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def _repo(self, repo):
        self._git(repo, "init", "-q")
        for sid in ("feed", "settings"):
            self._write(repo, "app/%s.tsx" % sid, "export default 1;\n")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-q", "-m", "base", date="2026-01-01T12:00:00")
        self._write(repo, "app/feed.tsx", "export default 2;\n")
        self._git(repo, "commit", "-qam", "feed", date="2026-01-05T12:00:00")

    def test_a_route_changed_after_updated_makes_its_screen_stale(self):
        with tmpdir() as repo:
            self._repo(repo)
            out = stale(SPEC, repo, CONFIG)
            first = subprocess.run(["git", "-C", repo, "rev-list", "--max-parents=0", "HEAD"],
                                   stdout=subprocess.PIPE).stdout.decode().strip()
            self.assertEqual(out, {"updated": "2026-01-02", "base": first, "stale_screens": ["feed"]})

    def test_head_is_the_build_commit_not_the_checkout(self):
        with tmpdir() as repo:
            self._repo(repo)
            built = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                                   stdout=subprocess.PIPE).stdout.decode().strip()
            self._git(repo, "checkout", "-q", "HEAD~1")  # the checkout sits on another commit than the build
            self._write(repo, "app/settings.tsx", "export default 3;\n")  # uncommitted: not in the build
            self.assertEqual(stale(SPEC, repo, CONFIG)["stale_screens"], ["settings"])
            self.assertEqual(stale(SPEC, repo, CONFIG, head=built)["stale_screens"], ["feed"])

    def test_a_spec_dated_today_is_current(self):
        with tmpdir() as repo:
            self._repo(repo)
            self._write(repo, "app/settings.tsx", "export default 3;\n")  # uncommitted
            today = SPEC.replace("2026-01-02", time.strftime("%Y-%m-%d"))
            self.assertEqual(stale(today, repo, CONFIG)["stale_screens"], [])

    def test_no_updated_line_is_an_error(self):
        with tmpdir() as repo:
            self._repo(repo)
            with self.assertRaises(FEError):
                stale(SPEC.replace("Updated:", "Changed:"), repo, CONFIG)


if __name__ == "__main__":
    unittest.main()
