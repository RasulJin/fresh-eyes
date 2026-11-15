import os
import unittest

from tests.fresh_eyes.helpers import make_png, tmpdir
from fe_common import FEError, read_json, write_json
from backlog import append, attach, index, match, next_id, render, set_status

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
        self.assertIn("- **Шаги**\n  1. Открыть профиль\n  2. Вкладка «Поездки»\n", text)
        self.assertIn("Факт: Третий ряд — полоска", text)
        self.assertIn("spec:profile-trips#must-1", text)

    def test_render_en(self):
        self.assertIn("- **Steps**\n  1. Открыть профиль", render(FINDING, "en-backlog", "BUG-1", "2026-10-19"))
        self.assertIn("  1. One step\n", render(dict(FINDING, steps="One step"), "en-backlog", "BUG-1", "2026-10-19"))

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
            self.assertEqual(rel, os.path.join("qa-backlog", "bug-54-setka-obrezana.png"))
            self.assertTrue(os.path.exists(os.path.join(d, rel)))

    def test_append_attaches_crop_under_the_new_id(self):
        with tmpdir() as d:
            p = write(d)
            src = make_png(os.path.join(d, "runs", "crop.png"), 10, 10)
            item = append(p, FINDING, "ru-backlog", "2026-10-19", src, os.path.join(d, "qa-backlog"))
            self.assertEqual(item, "BUG-54")
            self.assertTrue(os.path.exists(os.path.join(d, "qa-backlog", "bug-54-setka-obrezana.png")))
            self.assertIn("доказательство: qa-backlog/bug-54-setka-obrezana.png", read(p))

    def test_index_lists_open_entries_once(self):
        text = BACKLOG + ("\n- **IMP-15** — в поиске нет крестика\n\n"
                          "### BUG-60 · Подпись смещена — `open`\n- **Где** — Мастер, android, 2026-10-19 (fresh-eyes)\n\n"
                          "### BUG-61 · Снят — `снят` (не баг)\n\n"
                          "## Перепроверка\n- **BUG-22** (e-mail) — воспроизводится\n- **BUG-60** — всё ещё так\n")
        self.assertEqual(index(text), ["BUG-53 · пост открывается как «Post not found»",
                                       "IMP-15 · в поиске нет крестика",
                                       "BUG-60 · Подпись смещена - Мастер"])
        self.assertEqual(index(text, ["fixed"]), ["BUG-22 · на профиле e-mail", "IMP-12 · пересказ видео",
                                                  "IMP-15 · в поиске нет крестика"])  # no status counts too

    def test_unknown_status_refused(self):
        with tmpdir() as d:
            p = write(d)
            with self.assertRaises(FEError):
                set_status(p, "BUG-22", "done-ish", "n")



# backlog-index.md lines and IMP titles from the 0.5.9 run (2026-11-13)
INDEX = ["IMP-235 · Раздел «2. Какие данные» — сплошная простыня из 12 строк через точку с запятой - consent-doc",
         "IMP-161 · Тап по месту в шапке поста уводит из приложения в Google Maps",
         "IMP-238 · При прокрутке вниз уезжает вся шапка вместе с «Посты · Поездки» и не возвращается при прокрутке "
         "вверх - feed",
         "BUG-59 · Подсказки городов смешивают русский и английский - trip-wizard"]


class MatchTest(unittest.TestCase):
    def test_imps_an_open_entry_proposes_get_its_id_and_imp_md_lists_the_rest_in_full(self):
        def imp(fid, screen, title, **kw):
            return dict(dict(id=fid, kind="IMP", screen=screen, platform="android", severity="low", title=title,
                             verdict="pending"), **kw)
        with tmpdir() as run:
            a = os.path.join(run, "android", "consent-doc", "findings.json")
            b = os.path.join(run, "android", "feed-trips", "findings.json")
            write_json(a, {"findings": [
                imp("f1", "consent-doc", "Раздел «2. Какие данные» — 12 строк сплошным абзацем через точку с запятой"),
                imp("f2", "consent-doc", "Адрес поддержки в тексте нельзя нажать", actual="Обычный текст",
                    expected="Ссылка mailto", evidence=["android/consent-doc/default-none/screen_1000.png"]),
                imp("f3", "consent-doc", "Шапка не отделена от текста", backlog_id="IMP-236"),  # set by the verifier
                imp("f4", "consent-doc", "Раздел «2. Какие данные» сплошной", verdict="rejected"),
                dict(imp("f5", "consent-doc", "Раздел «2. Какие данные» обрезан"), kind="BUG")],
                "questions": [{"text": "Ссылкой?", "finding": "f2"}]})
            write_json(b, {"findings": [
                imp("f1", "post-detail", "Кнопка места уводит из приложения в Google Maps"),  # no screen in the entry
                imp("f2", "feed-trips", "Переключатель «Посты · Поездки» уезжает с шапкой и не возвращается"),
                imp("f3", "post-detail", "Тап по фото уводит в галерею")]})  # 2 stems shared: too few
            out = match(INDEX, [a, b], os.path.join(run, "imp.md"), "ru")
            self.assertEqual(out["new"], 3)
            self.assertEqual((out["known"], out["matched"]), (3, 2))
            ids = [f.get("backlog_id") for p in (a, b) for f in read_json(p)["findings"]]
            # IMP-238 is the feed screen's: a look-alike on another screen stays new
            self.assertEqual(ids, ["IMP-235", None, "IMP-236", None, None, "IMP-161", None, None])
            text = read(os.path.join(run, "imp.md"))
            self.assertTrue(text.startswith("# Новые улучшения: 3\n\n## 1. Адрес поддержки в тексте нельзя нажать\n"
                                            "consent-doc · android · low · `android/consent-doc/findings.json#f2`\n"
                                            "Обычный текст\n→ Ссылка mailto\n? Ссылкой?\n"
                                            "`android/consent-doc/default-none/screen_1000.png`\n"), text)
            self.assertIn("# Уже в бэклоге: 3\n", text)
            self.assertIn("— IMP-161 «Тап по месту в шапке поста уводит из приложения в Google Maps» · "
                          "`android/feed-trips/findings.json#f1`", text)
            self.assertNotIn("обрезан", text)

    def test_a_pending_bug_gets_the_open_bug_entry_of_its_screen_before_the_verifier(self):
        # 0.5.11: trip-wizard#f1 went to the owner as a question while BUG-44 was open
        lines = INDEX + ["BUG-44 · (подсказки городов в мастере обрезаны над «Дальше» при открытой клавиатуре)",
                         "BUG-65 · Свайп влево по затемнению не закрывает меню - side-menu"]
        def bug(fid, screen, title, **kw):
            return dict(dict(id=fid, kind="BUG", screen=screen, severity="medium", title=title, verdict="pending"),
                        **kw)
        with tmpdir() as run:
            p = os.path.join(run, "android", "trip-wizard", "findings.json")
            write_json(p, {"findings": [
                bug("f1", "trip-wizard", "При открытой клавиатуре подсказки городов уходят под кнопку «Дальше»"),
                bug("f2", "trip-wizard", "Подсказки городов смешивают русский и английский", verdict="unsure"),
                bug("f3", "side-menu", "Свайп влево по меню закрывает не то меню"),  # BUG-65's screen and words
                bug("f4", "feed", "Свайп влево по затемнению не закрывает меню"),  # BUG-65 is side-menu's
                bug("f5", "trip-wizard", "Подсказки городов мелкие"),  # 2 stems shared: too few
                bug("f6", "consent-doc", "Раздел «2. Какие данные» обрезан")]})  # an IMP entry never holds a BUG
            self.assertEqual(match(lines, [p]), {"matched": 0, "bugs": 2})  # no --out: no imp.md
            self.assertEqual([f.get("backlog_id") for f in read_json(p)["findings"]],
                             ["BUG-44", None, "BUG-65", None, None, None])  # only pending ones: f2 is ruled
            self.assertFalse(os.path.exists(os.path.join(run, "imp.md")))

    def test_index_reads_the_backlog_at_a_commit_and_names_screens_by_id(self):
        import subprocess
        with tmpdir() as d:
            p = write(d, "### BUG-60 · Подпись смещена — `open`\n- **Где** — Мастер поездки, android\n")
            git = ["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run(git + ["init", "-q"], check=True)
            subprocess.run(git + ["add", "."], check=True)
            subprocess.run(git + ["commit", "-qm", "b"], check=True)
            write(d, "### BUG-61 · Новее сборки — `open`\n")  # the checkout moved on: not what was built
            from backlog import read_at
            text = read_at(p, "HEAD")
            self.assertEqual(index(text, names={"Мастер поездки": "trip-wizard"}),
                             ["BUG-60 · Подпись смещена - trip-wizard"])
            self.assertIn("BUG-61", read_at(p))
            with self.assertRaises(FEError):
                read_at(p, "no-such-ref")


if __name__ == "__main__":
    unittest.main()
