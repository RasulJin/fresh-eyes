import os
import unittest

from tests.fresh_eyes.helpers import make_png, tmpdir
from fe_common import FEError
from backlog import append, attach, next_id, render, set_status

BACKLOG = """# QA-бэклог

## Баги

### BUG-3 · Пост в ленте обрезан — `verified` (пп. 1–4)
- **Где** — Лента

### BUG-17 · Чужое фото в профиле — `verified`
- x

### BUG-17 · Vio рассказывает про механику — `fixed` (main afead40)
- y

### BUG-22 — на профиле e-mail — `fixed` (в main: `9028e657`, сверено 2026-10-05)
- z

### BUG-53 — пост открывается как «Post not found» — `open` (minor)

### IMP-12 — пересказ видео — `fixed` частично (`38003b71`)
"""

FINDING = {"kind": "BUG", "title": "Сетка обрезана", "screen": "Профиль → Поездки", "platform": "android",
           "steps": ["Открыть профиль", "Вкладка «Поездки»"], "expected": "Все ряды видны",
           "actual": "Третий ряд — полоска", "confidence": 0.9, "rule": "spec:profile-trips#must-1",
           "evidence": ["runs/x/crops/clipped-1.png"]}


def write(d, text=BACKLOG):
    p = os.path.join(d, "qa-backlog.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class BacklogTest(unittest.TestCase):
    def test_next_id_with_duplicates(self):
        self.assertEqual(next_id(BACKLOG, "BUG"), 54)
        self.assertEqual(next_id(BACKLOG, "IMP"), 13)
        self.assertEqual(next_id("# empty", "BUG"), 1)

    def test_next_id_sees_other_entry_formats(self):
        text = BACKLOG + "\n## IMP-13 — разбор\n- **IMP-14** — `fixed` клавиатура\n- **IMP-19** — счётчик\n"
        self.assertEqual(next_id(text, "IMP"), 20)

    def test_set_status_bullet_entry(self):
        with tmpdir() as d:
            p = write(d, BACKLOG + "\n- **IMP-14** — `fixed` (2026-10-03) — клавиатуру нельзя закрыть\n")
            set_status(p, "IMP-14", "verified", "ok")
            self.assertIn("- **IMP-14** — `verified` (2026-10-03)", read(p))

    def test_render_ru(self):
        text = render(FINDING, "ru-backlog", "BUG-54", "2026-10-19")
        self.assertTrue(text.startswith("### BUG-54 · Сетка обрезана — `open`"))
        self.assertIn("- **Шаги** — Открыть профиль → Вкладка «Поездки»", text)
        self.assertIn("Факт: Третий ряд — полоска", text)
        self.assertIn("spec:profile-trips#must-1", text)

    def test_render_en(self):
        self.assertIn("- **Steps** —", render(FINDING, "en-backlog", "BUG-1", "2026-10-19"))

    def test_render_unknown_template(self):
        with self.assertRaises(FEError):
            render(FINDING, "jira", "BUG-1", "2026-10-19")

    def test_append_assigns_next_number(self):
        with tmpdir() as d:
            p = write(d)
            self.assertEqual(append(p, FINDING, "ru-backlog", "2026-10-19"), "BUG-54")
            self.assertEqual(append(p, dict(FINDING, kind="IMP", title="Показать один раз"), "ru-backlog",
                                    "2026-10-19"), "IMP-13")
            text = read(p)
            self.assertIn("\n\n### BUG-54 · Сетка обрезана — `open`", text)
            self.assertTrue(text.startswith("# QA-бэклог"))

    def test_set_status_unique(self):
        with tmpdir() as d:
            p = write(d)
            set_status(p, "BUG-22", "verified", "2026-10-19, fresh-eyes-pixel, verified")
            lines = read(p).splitlines()
            i = next(k for k, l in enumerate(lines) if l.startswith("### BUG-22"))
            self.assertIn("`verified` (в main: `9028e657`", lines[i])
            self.assertEqual(lines[i + 1], "- **Перепроверка fresh-eyes** — 2026-10-19, fresh-eyes-pixel, verified")

    def test_set_status_duplicate_refuses(self):
        with tmpdir() as d:
            p = write(d)
            with self.assertRaises(FEError) as e:
                set_status(p, "BUG-17", "verified", "x")
            self.assertIn("line", str(e.exception))
            self.assertEqual(read(p), BACKLOG)

    def test_set_status_duplicate_with_line(self):
        with tmpdir() as d:
            p = write(d)
            line = BACKLOG.splitlines().index("### BUG-17 · Vio рассказывает про механику — `fixed` (main afead40)") + 1
            set_status(p, "BUG-17", "verified", "ok", line=line)
            self.assertIn("### BUG-17 · Vio рассказывает про механику — `verified` (main afead40)", read(p))
            self.assertIn("### BUG-17 · Чужое фото в профиле — `verified`\n- x", read(p))

    def test_set_status_reopen(self):
        with tmpdir() as d:
            p = write(d)
            set_status(p, "BUG-3", "open", "воспроизвёлся на сборке abc123")
            self.assertIn("### BUG-3 · Пост в ленте обрезан — `open` (пп. 1–4)", read(p))

    def test_set_status_not_found(self):
        with tmpdir() as d:
            with self.assertRaises(FEError):
                set_status(write(d), "BUG-99", "verified", "x")

    def test_bug_prefix_does_not_match_longer_number(self):
        with tmpdir() as d:
            p = write(d, "### BUG-5 · a — `open`\n\n### BUG-53 · b — `open`\n")
            set_status(p, "BUG-5", "fixed", "n")
            self.assertIn("### BUG-53 · b — `open`", read(p))

    def test_attach_copies_and_returns_relative_link(self):
        with tmpdir() as d:
            src = make_png(os.path.join(d, "crop.png"), 10, 10)
            rel = attach(src, os.path.join(d, "qa-backlog"), "BUG-54", "Сетка обрезана", d)
            self.assertEqual(rel, os.path.join("qa-backlog", "bug-54-сетка-обрезана.png"))
            self.assertTrue(os.path.exists(os.path.join(d, rel)))


if __name__ == "__main__":
    unittest.main()
