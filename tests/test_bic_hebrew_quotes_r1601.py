"""R1601: Ben Ish Chai parasha Shoftim (archive dates 2026-08-09..13) failed in every run with "Expecting ',' delimiter: line 5 column N"
even after the re-ask. tests/fixtures/bic_shoftim_raw_answers_r1601.json holds the REAL model answers, VERBATIM: a capture-only workflow
run (41269616920, engine = main dcfaf78, nothing saved) called the real benishchai_agent for the 5 dates -- 10 answers (each date: the
answer + the re-ask), all 10 unparseable on main, every one because of a Hebrew abbreviation written with a bare " (הרמב"ם x10, זצ"ל x1).
Offline: no network, no API key (an in-memory anthropic stand-in is used only if the SDK is absent)."""
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
try:
    import anthropic  # noqa: F401
except ImportError:            # only so the tests run without the SDK; CI installs the pinned one
    sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=lambda **kw: None, __version__="test-stub")
import requests  # noqa: E402
import engine as E  # noqa: E402

FX = os.path.join(ROOT, "tests", "fixtures")
REAL = json.load(open(os.path.join(FX, "bic_shoftim_raw_answers_r1601.json"), encoding="utf-8"))
ANSWERS = REAL["answers"]
SOURCE = open(os.path.join(FX, "bic_shoftim_1st_year_excerpt.txt"), encoding="utf-8").read().strip()
KEYS = {"title", "halacha1", "halacha2", "connection", "disclaimer"}
# the recorded archive errors of the 5 dates (benishchai.json on main) have exactly this shape
RECORDED = "Expecting ',' delimiter: line 5 column"


def reply(text):
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)])


def unfenced(raw):
    """what _answer_json parses: the answer without its ```json fence (all 10 real answers are fenced)"""
    return raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = {n: getattr(E, n) for n in ("llm_create", "anthropic", "sef_json")}
        self.saved_get = requests.get

        def no_network(*a, **kw):
            raise AssertionError("a test reached the network: %r" % (a,))
        requests.get = no_network
        E.FAILURES.clear()
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="r1601_")
        os.chdir(self.tmp)
        E.anthropic = types.SimpleNamespace(Anthropic=lambda **kw: object())

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(E, n, v)
        requests.get = self.saved_get
        E.FAILURES.clear()
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def answers(self, *texts):
        self.calls = 0
        q = list(texts)

        def fake(client, **kw):
            self.calls += 1
            return reply(q.pop(0))
        E.llm_create = fake


class RealAnswers(Base):
    def test_1_fixture_is_the_capture_and_every_answer_fails_like_the_archive_errors(self):
        self.assertEqual(len(ANSWERS), 10)
        self.assertEqual(sorted({a["date"] for a in ANSWERS}), ["2026-08-09", "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"])
        self.assertTrue(all(a["parasha"] == "שופטים" and a["stop_reason"] == "end_turn" for a in ANSWERS))
        self.assertEqual(sum(a["reask"] for a in ANSWERS), 5)            # each date: the answer, then the re-ask
        self.assertIn("41269616920", REAL["source"])
        for a in ANSWERS:
            with self.assertRaises(json.JSONDecodeError, msg=a["date"]) as cm:
                json.loads(unfenced(a["raw"]))
            self.assertTrue(str(cm.exception).startswith(RECORDED), "%s %s" % (a["date"], cm.exception))

    def test_2_every_real_answer_now_reads_as_a_complete_record(self):
        for a in ANSWERS:
            d = E._answer_json(reply(a["raw"]), empty_ok=True)
            self.assertEqual(set(d), KEYS, a["date"])
            self.assertIsNone(E.incomplete_content({"date": a["date"], "content": d}), a["date"])
            self.assertIn("הרמב״ם", json.dumps(d, ensure_ascii=False), a["date"])

    def test_3_the_repair_changes_only_the_abbreviation_quotes(self):
        total = 0
        for a in ANSWERS:
            raw = unfenced(a["raw"])
            fixed = E._repair_hebrew_quotes(raw)
            self.assertEqual(len(fixed), len(raw))
            diffs = [(x, y) for x, y in zip(raw, fixed) if x != y]
            self.assertTrue(diffs and all(p == ('"', "״") for p in diffs), a["date"])
            total += len(diffs)
        self.assertEqual(total, 11)                                       # הרמב"ם x10 + זצ"ל x1, nothing else

    def test_4_benishchai_agent_saves_a_complete_record_from_a_real_answer_in_one_call(self):
        E.sef_json = lambda url, **kw: {"he": [SOURCE]}
        a = ANSWERS[0]
        self.answers(a["raw"])
        rec = E.benishchai_agent(datetime.date(2026, 8, 9), "שופטים")
        self.assertIsNotNone(rec)
        self.assertEqual(self.calls, 1)                                   # no re-ask needed any more
        self.assertEqual(rec["ref"], "Ben Ish Hai, Halachot 1st Year, Shoftim")
        saved = json.load(open(os.path.join(self.tmp, E.BIC_OUT), encoding="utf-8"))
        self.assertEqual([r["date"] for r in saved], ["2026-08-09"])
        self.assertIsNone(E.incomplete_content(saved[0]))
        self.assertEqual(E.FAILURES, [])


class Unchanged(Base):
    def test_5_a_valid_answer_is_read_exactly_as_before(self):
        good = {"title": "בן איש חי — פרשת כי תצא", "halacha1": {"title": "משקל חסר", "explanation": 'אמר "לא יהיה לך" \\ וגם ז״ל'},
                "halacha2": {"title": "ב", "explanation": "ג"}, "connection": "ד", "disclaimer": "ה"}
        for text in (json.dumps(good, ensure_ascii=False, indent=2), json.dumps(good, ensure_ascii=False)):
            self.assertEqual(E._answer_json(reply(text), empty_ok=True), good)

    def test_6_the_repair_never_touches_a_structural_quote(self):
        doc = {"כותרת": "ערך", "רשימה": ["א", "ב ג", "ד."], "פנימי": {"מפתח": "ערך עם רווח", "ריק": ""}, "n": 1}
        for text in (json.dumps(doc, ensure_ascii=False, indent=2), json.dumps(doc, ensure_ascii=False),
                     json.dumps(doc, ensure_ascii=False, separators=(",", ":"))):
            self.assertEqual(E._repair_hebrew_quotes(text), text)

    def test_7_an_answer_the_repair_cannot_fix_still_fails_loudly(self):
        bad = unfenced(ANSWERS[0]["raw"]).replace('"connection": "', '"connection": "he said "yes" then ', 1)
        self.assertNotEqual(bad, unfenced(ANSWERS[0]["raw"]))
        with self.assertRaises(json.JSONDecodeError):
            E._answer_json(reply(bad), empty_ok=True)

    def test_8_two_unparseable_answers_raise_and_the_raw_answer_is_printed(self):
        bad = unfenced(ANSWERS[0]["raw"]).replace('"connection": "', '"connection": "he said "yes" then ', 1)
        self.answers(bad, bad)
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(json.JSONDecodeError):
            E.llm_json(object(), lambda m: E._answer_json(m, empty_ok=True), model="m", max_tokens=5, temperature=0, system="s",
                       messages=[{"role": "user", "content": "q"}])
        self.assertEqual(self.calls, 2)
        self.assertIn("UNPARSEABLE_MODEL_JSON (first 800 chars): {", out.getvalue())


if __name__ == "__main__":
    unittest.main()
