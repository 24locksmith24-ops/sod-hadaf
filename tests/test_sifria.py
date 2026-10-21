"""Sifria: one permanent file per lesson, an index by masechet and daf, never overwritten. Offline."""
import datetime
import json
import os
import shutil
import sys
import types
import unittest
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
import engine as E  # noqa: E402
import lessons_store as LS  # noqa: E402
import backfill_lessons as B  # noqa: E402


def full(title="כותרת"):
    return {"title": title, "intro": "פתיחה " * 100, "daf": {"teaching": "הוראה " * 100}, "takeaway": "לקח"}


def rec(date="2026-10-21", ref="Bekhorot 33", he="בכורות ל״ג", lesson=None):
    return {"date": date, "ref": ref, "he_ref": he, "lesson": lesson or full(), "theme": "t", "shared_words": [], "codes_raw": {}, "sources_used": [], "policy": {}}


class Tmp(unittest.TestCase):
    def setUp(self):
        self.old = os.getcwd()
        self.d = os.path.join(ROOT, "tests", "_tmp_" + uuid.uuid4().hex[:8])
        os.makedirs(self.d)
        os.chdir(self.d)
        E.FAILURES.clear()

    def tearDown(self):
        os.chdir(self.old)
        shutil.rmtree(self.d, ignore_errors=True)
        E.FAILURES.clear()


class Store(Tmp):
    def test_one_file_per_lesson_named_by_masechet_and_daf(self):
        st, p = LS.save_lesson(rec())
        self.assertEqual((st, p.replace(os.sep, "/")), ("created", "lessons/Bekhorot/033.json"))
        body = json.load(open(p, encoding="utf-8"))
        self.assertEqual((body["date"], body["ref"], body["revision"], body["schema"]), ("2026-10-21", "Bekhorot 33", 1, 1))
        self.assertEqual(len(body["lesson_sha256"]), 64)

    def test_never_overwritten(self):
        _, p = LS.save_lesson(rec(lesson=full("ראשון")))
        before = open(p, "rb").read()
        st, p2 = LS.save_lesson(rec(date="2026-10-22", lesson=full("שני")))
        self.assertEqual((st, p2), ("exists", p))
        self.assertEqual(open(p, "rb").read(), before)

    def test_exclusive_create_refuses_a_second_writer(self):
        _, p = LS.save_lesson(rec())
        with self.assertRaises(FileExistsError):
            open(p, "x")

    def test_deliberate_revision_keeps_the_original(self):
        _, p = LS.save_lesson(rec(lesson=full("ראשון")))
        before = open(p, "rb").read()
        st, p2 = LS.save_lesson(rec(lesson=full("מתוקן")), revise=True)
        self.assertEqual(st, "revision")
        self.assertTrue(p2.endswith("033.r2.json"))
        self.assertEqual(open(p, "rb").read(), before)
        st, p3 = LS.save_lesson(rec(lesson=full("עוד")), revise=True)
        self.assertTrue(p3.endswith("033.r3.json"))

    def test_a_stub_is_refused(self):
        with self.assertRaises(LS.StubRefused):
            LS.save_lesson(rec(lesson={"title": "x", "_error": "boom"}))
        with self.assertRaises(LS.StubRefused):
            LS.save_lesson(rec(lesson={"title": "short"}))
        self.assertFalse(os.path.exists("lessons"))

    def test_index_by_masechet_and_daf(self):
        LS.save_lesson(rec())
        LS.save_lesson(rec(date="2026-10-18", ref="Bekhorot 30", he="בכורות ל׳"))
        LS.save_lesson(rec(date="2026-09-17", ref="Chullin 140", he="חולין ק״מ"))
        LS.save_lesson(rec(lesson=full("מתוקן")), revise=True)
        idx = json.load(open("lessons/index.json", encoding="utf-8"))
        self.assertEqual(sorted(idx["masechtot"]), ["Bekhorot", "Chullin"])
        bek = idx["masechtot"]["Bekhorot"]
        self.assertEqual((bek["he"], bek["count"], list(bek["dafim"])), ("בכורות", 2, ["30", "33"]))
        self.assertEqual(bek["dafim"]["33"]["file"], "lessons/Bekhorot/033.json")
        self.assertEqual(bek["dafim"]["33"]["revisions"], ["lessons/Bekhorot/033.r2.json"])
        self.assertEqual(idx["masechtot"]["Chullin"]["he"], "חולין")

    def test_ref_parsing(self):
        self.assertEqual(LS.parse_ref("Bekhorot 33"), ("Bekhorot", 33))
        self.assertEqual(LS.parse_ref("Bava Batra 7b"), ("Bava_Batra", 7))
        with self.assertRaises(ValueError):
            LS.parse_ref("Genesis 12:1")


class View(Tmp):
    def test_import_is_idempotent_and_skips_stubs(self):
        json.dump([rec(), rec(date="2026-10-20", ref="Bekhorot 32", he="בכורות ל״ב", lesson={"title": "x", "_error": "boom"})], open("discoveries.json", "w", encoding="utf-8"), ensure_ascii=False)
        self.assertEqual(len(LS.import_from_view("discoveries.json")), 1)
        self.assertEqual(LS.import_from_view("discoveries.json"), [])
        self.assertFalse(os.path.exists("lessons/Bekhorot/032.json"))

    def test_sync_view_replaces_a_stub_but_never_a_full_entry(self):
        stub = rec(lesson={"title": "x", "_error": "boom"})
        json.dump([stub], open("discoveries.json", "w", encoding="utf-8"), ensure_ascii=False)
        self.assertTrue(LS.sync_view(rec(lesson=full("חדש")), "discoveries.json"))
        data = json.load(open("discoveries.json", encoding="utf-8"))
        self.assertEqual(data[0]["lesson"]["title"], "חדש")
        self.assertFalse(LS.sync_view(rec(lesson=full("אחר")), "discoveries.json"))
        self.assertEqual(json.load(open("discoveries.json", encoding="utf-8"))[0]["lesson"]["title"], "חדש")


class EngineSave(Tmp):
    def test_save_writes_the_permanent_file_and_the_view(self):
        self.assertTrue(E.save(rec()))
        self.assertTrue(os.path.exists("lessons/Bekhorot/033.json"))
        self.assertEqual(json.load(open(E.OUT, encoding="utf-8"))[0]["ref"], "Bekhorot 33")
        self.assertEqual(E.FAILURES, [])

    def test_second_save_for_the_same_daf_does_not_replace_the_lesson(self):
        E.save(rec(lesson=full("ראשון")))
        E.save(rec(date="2026-10-22", lesson=full("שני")))
        self.assertEqual(json.load(open("lessons/Bekhorot/033.json", encoding="utf-8"))["lesson"]["title"], "ראשון")

    def test_save_refuses_a_stub_and_writes_nothing(self):
        self.assertFalse(E.save(rec(lesson={"title": "x", "_error": "boom"})))
        self.assertFalse(os.path.exists("lessons"))
        self.assertFalse(os.path.exists(E.OUT))
        self.assertEqual(len(E.FAILURES), 1)

    def test_generate_lesson_returns_a_stub_that_save_then_refuses(self):
        class Boom:
            class messages:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("api down")
        real = E.anthropic
        E.anthropic = types.SimpleNamespace(Anthropic=lambda *a, **k: Boom, __version__="0.125.0")
        try:
            daf = {"he_ref": "בכורות לג", "ref": "Bekhorot 33", "hebrew": "מילה " * 50}
            lesson, conn = E.generate_lesson(daf, [], [], None, [], [], {}, [], [])
        finally:
            E.anthropic = real
        self.assertTrue(lesson.get("_error"))
        self.assertFalse(E.save(rec(lesson=lesson)))


class BackfillTargets(unittest.TestCase):
    def test_targets_skip_friday_saturday_holidays_and_full_lessons(self):
        view = [rec(date="2026-10-14", lesson=full()),                                 # full: not a target
                rec(date="2026-10-13", lesson={"title": "x", "_error": "boom"})]       # stub: target
        holiday = lambda d: "חג" if d == datetime.date(2026, 10, 15) else None
        got = B.targets(datetime.date(2026, 10, 12), datetime.date(2026, 10, 20), view, resting=holiday)
        days = {d.isoformat(): r for d, r in got}
        # Mon 12 missing, Tue 13 stub, Wed 14 full, Thu 15 holiday, Fri 16 / Sat 17 rest, Sun 18, Mon 19, Tue 20 missing
        self.assertEqual(days, {"2026-10-12": "missing", "2026-10-13": "stub", "2026-10-18": "missing", "2026-10-19": "missing", "2026-10-20": "missing"})


if __name__ == "__main__":
    unittest.main()
