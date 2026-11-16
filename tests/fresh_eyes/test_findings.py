import os
import unittest

from tests.fresh_eyes.helpers import tmpdir
from fe_common import read_json, write_json
from findings import known, same, shared


def bug(fid, screen, title, **kw):
    f = {"id": fid, "kind": "BUG", "severity": "low", "confidence": 0.6, "screen": screen, "platform": "android",
         "title": title, "rule": "spec:G-copy", "verdict": "pending"}
    f.update(kw)
    return f


DEMO_1 = "Экран поездки смешивает русский и английский: «Обзор поездки», заголовки дней по-английски"
DEMO_2 = "Названия дней и остановок по-английски в русском интерфейсе"
ALMATY = "Русская карточка маршрута «4 дня в Алматы» показывает место по-английски: «Almaty, Kazakhstan»"
OVERVIEW = "Карточка «Обзор поездки» на русском интерфейсе показывает английские строки"


def put(run, screen, findings, name="findings.json", **extra):
    p = os.path.join(run, "android", screen, name)
    write_json(p, dict({"findings": findings}, **extra))
    return p


class SharedTest(unittest.TestCase):
    def test_one_case_in_other_words(self):
        # 0.5.12 run: the English demo trip on trip-detail and trip-map, Almaty in the guide and the feed
        self.assertTrue(shared(bug("f1", "trip-detail", DEMO_1), bug("f2", "trip-map", DEMO_2)))
        self.assertTrue(shared(bug("r1", "feed", "«Спланировать» открывает Гид: «Спланируй поездку в Almaty, Казахстан»"),
                               bug("f2", "vio-guide", "Сообщение «Спланируй поездку в Almaty, Казахстан» смешивает")))

    def test_quotes_of_both_titles_must_meet(self):
        # both mix languages, but on different on-screen text: two cases
        self.assertFalse(shared(bug("f1", "trip-detail", OVERVIEW), bug("f2", "vio-guide", ALMATY)))

    def test_another_platform_or_few_words(self):
        self.assertFalse(shared(bug("f1", "a", DEMO_1), bug("f2", "b", DEMO_2, platform="ios")))
        self.assertFalse(shared(bug("f1", "a", "Кнопка обрезана"), bug("f2", "b", "Кнопка обрезана снизу")))


class SameTest(unittest.TestCase):
    def test_links_across_folders_and_names_who_needs_a_verifier(self):
        with tmpdir() as run:
            a = put(run, "trip-detail", [bug("f1", "trip-detail", DEMO_1)])
            b = put(run, "trip-map", [bug("f2", "trip-map", DEMO_2), bug("f3", "trip-map", ALMATY)])
            c = put(run, "trips", [bug("f1", "trip-detail", DEMO_1 + " ещё раз")])
            out = same(run, [a, b, c])
            fb, fc = read_json(b)["findings"], read_json(c)["findings"][0]
        ref = os.path.join("android", "trip-detail", "findings.json") + "#f1"
        self.assertEqual((fb[0]["same_as"], fb[0]["same_as_auto"]), (ref, True))
        self.assertNotIn("same_as", fb[1])  # another case
        self.assertEqual(fc["same_as"], ref)
        self.assertEqual((out["linked"], out["verify"]), (2, [a, b]))  # trips holds only a linked copy

    def test_a_held_or_ruled_finding_stays_the_one_checked_and_its_ruling_is_copied(self):
        with tmpdir() as run:
            held = put(run, "trip-detail", [bug("f1", "trip-detail", DEMO_1)])
            new = put(run, "trip-map", [bug("f2", "trip-map", DEMO_2)])
            same(run, [new])  # trip-detail is with its verifier: read, never written
            self.assertNotIn("same_as_auto", read_json(held)["findings"][0])
            self.assertEqual(read_json(new)["findings"][0]["verdict"], "pending")
            write_json(held, {"findings": [bug("f1", "trip-detail", DEMO_1, verdict="rejected", severity="medium",
                                               verify_note="test data: a demo trip in English")]})
            out = same(run, [held, new])
            f = read_json(new)["findings"][0]
        self.assertEqual((f["verdict"], f["severity"], out["settled"], out["verify"]), ("rejected", "medium", 1, []))
        self.assertTrue(f["verify_note"].startswith("test data:"))  # a flaky: or test data: note keeps its word

    def test_a_target_ruled_again_moves_its_copy(self):
        # 0.5.13 acceptance: the target changed its ruling after a CONFLICT round, the copy kept the old one
        with tmpdir() as run:
            a = put(run, "trip-detail", [bug("f1", "trip-detail", DEMO_1, verdict="rejected",
                                             verify_note="flaky: once")])
            b = put(run, "trip-map", [bug("f2", "trip-map", DEMO_2)])
            same(run, [a, b])
            write_json(a, {"findings": [bug("f1", "trip-detail", DEMO_1, verdict="confirmed", severity="medium",
                                            verify_note="reproduced 2 of 2")]})
            self.assertEqual(same(run, [a, b])["settled"], 1)
            f = read_json(b)["findings"][0]
            self.assertEqual(same(run, [a, b])["settled"], 0)  # nothing new: the note is not stacked
        self.assertEqual((f["verdict"], f["severity"]), ("confirmed", "medium"))
        self.assertTrue(f["verify_note"].startswith("reproduced 2 of 2 (same case:"))

    def test_a_target_turned_imp_takes_the_link_along(self):
        with tmpdir() as run:
            a = put(run, "trip-detail", [bug("f1", "trip-detail", DEMO_1)])
            b = put(run, "trip-map", [bug("f2", "trip-map", DEMO_2)])
            same(run, [a, b])
            write_json(a, {"findings": [bug("f1", "trip-detail", DEMO_1, kind="IMP")]})
            same(run, [a, b])
            self.assertEqual(read_json(b)["findings"][0]["kind"], "IMP")

    def test_the_same_folder_is_the_verifiers_and_requests_need_one(self):
        with tmpdir() as run:
            a = put(run, "trip-detail", [bug("f1", "trip-detail", DEMO_1)])
            b = put(run, "trip-detail", [bug("f1", "trip-detail", DEMO_2)], name="findings-2.json",
                    requests=[{"pack": "x", "do": "crop"}])
            out = same(run, [a, b])
        self.assertEqual((out["linked"], sorted(out["verify"])), (0, sorted([a, b])))


class KnownTest(unittest.TestCase):
    def test_known_questions_and_test_data_once(self):
        with tmpdir() as run:
            put(run, "feed", [bug("r1", "feed", "Almaty", verdict="rejected", verify_note="known candidate: x")],
                questions=[{"class": "known", "text": "feed: Almaty stays Latin — planTripPlace.ts: «city as is»"},
                           {"class": "spec-text", "text": "not this"}])
            put(run, "trip-map", [bug("f2", "trip-map", DEMO_2, verdict="rejected",
                                      verify_note="test data: «4 Days in Istanbul», a demo trip"),
                                  bug("f3", "trip-map", DEMO_1, verdict="rejected", same_as_auto=True,
                                      verify_note="test data: copy (same case: x)")])
            out = os.path.join(run, "known-proposed.md")
            self.assertEqual(known(run, out)["added"], 2)
            self.assertEqual(known(run, out)["added"], 0)
            with open(out, encoding="utf-8") as f:
                text = f.read()
        self.assertIn("- feed: Almaty stays Latin", text)
        self.assertIn("- trip-map: «%s» — test data: «4 Days in Istanbul»" % DEMO_2, text)
        self.assertNotIn("not this", text)


if __name__ == "__main__":
    unittest.main()
