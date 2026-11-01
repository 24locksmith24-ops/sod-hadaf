"""R1601: Ben Ish Chai parasha Shoftim (archive dates 2026-08-09..13) failed in every run with "Expecting ',' delimiter: line 5 column N"
even after the re-ask: the source sent to the model (Ben Ish Hai, Halachot 1st Year, Shoftim; 39 ASCII-quote abbreviations in its first
4000 chars) has Hebrew abbreviations and a quoted title written with a bare " (ע"י, ז"ל, חז"ל, ב"שמחת יום טוב"), and the model copies
them into its JSON strings. The raw failing answers were never saved (the engine kept only the error), so the answers below are
model-shaped JSON built from the REAL source phrases in tests/fixtures/bic_shoftim_1st_year_excerpt.txt (Sefaria, public-domain text);
test 1 checks they fail EXACTLY like the recorded errors (same error, line 5) before the fix is asked to read them.
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

SOURCE = open(os.path.join(ROOT, "tests", "fixtures", "bic_shoftim_1st_year_excerpt.txt"), encoding="utf-8").read().strip()

# pretty-printed like the model's answers (the recorded errors are all on line 5 = halacha1.explanation)
ANSWER_ABBREV = '''{
  "title": "בן איש חי — פרשת שופטים",
  "halacha1": {
    "title": "ברכת אירוסין",
    "explanation": "תקנו חז"ל ברכת אירוסין: כמו דמברכין ברכת הנהנין קודם שנהנה, כן מברכין על הנאה של האשה קודם אירוסין. המקדש ע"י עצמו מברך, ואם ע"י שליח – השליח מברך במקומו."
  },
  "halacha2": {
    "title": "הזריזות בנישואין",
    "explanation": "כל הקודם לישא אשה אחר שנעשה בר מצוה הרי זה משובח, כדברי רב חסדא בגמרא. ומי שיש לו סיבה הכרחית להקדים, יעיין בדברי הרב יום טוב אלנקוה ז"ל."
  },
  "connection": "שתי ההלכות עוסקות בקדושת הבית מראשיתו: הזריזות להינשא והברכה על האירוסין.",
  "disclaimer": "לימוד בלבד; להוראה למעשה — פנו לרב מוסמך"
}'''
# the quoted title of the same source: ב"שמחת יום טוב" (opening " right after a letter, closing " before a space + word)
ANSWER_TITLE = ANSWER_ABBREV.replace('יעיין בדברי הרב יום טוב אלנקוה ז"ל."', 'יעיין בספר ב"שמחת יום טוב" כיצד יתנהג בענין הכתובה."')
# a quoted phrase opened after a space: הרב "שמחת יום טוב" כיצד
ANSWER_SPACE_OPEN = ANSWER_ABBREV.replace('יעיין בדברי הרב יום טוב אלנקוה ז"ל."', 'יעיין בספרו של הרב "שמחת יום טוב" כיצד לנהוג."')
KEYS = {"title", "halacha1", "halacha2", "connection", "disclaimer"}


def reply(text):
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)])


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


class Fixtures(Base):
    def test_1_fixture_phrases_are_real_and_the_answer_fails_like_the_recorded_errors(self):
        for phrase in ('ע"י', 'ז"ל', 'חז"ל', 'ב"שמחת יום טוב"'):
            self.assertIn(phrase, SOURCE)
        with self.assertRaises(json.JSONDecodeError) as cm:
            json.loads(ANSWER_ABBREV)
        self.assertTrue(str(cm.exception).startswith("Expecting ',' delimiter: line 5 column"), str(cm.exception))


class Parse(Base):
    def check_complete(self, d):
        self.assertEqual(set(d), KEYS)
        self.assertIsNone(E.incomplete_content({"date": "2026-08-09", "content": d}))

    def test_2_abbreviations_parse_as_gershayim(self):
        d = E._answer_json(reply(ANSWER_ABBREV), empty_ok=True)
        self.check_complete(d)
        self.assertIn("חז״ל", d["halacha1"]["explanation"])
        self.assertIn("ע״י", d["halacha1"]["explanation"])
        leaves = [d["title"], d["connection"], d["disclaimer"]] + [v for h in ("halacha1", "halacha2") for v in d[h].values()]
        self.assertFalse(any('"' in v for v in leaves), "an ASCII quote survived inside a value")

    def test_3_quoted_title_parses(self):
        d = E._answer_json(reply(ANSWER_TITLE), empty_ok=True)
        self.check_complete(d)
        self.assertIn("ב״שמחת יום טוב״ כיצד", d["halacha2"]["explanation"])

    def test_4_quoted_phrase_opened_after_a_space_parses(self):
        d = E._answer_json(reply(ANSWER_SPACE_OPEN), empty_ok=True)
        self.check_complete(d)
        self.assertIn("הרב ״שמחת יום טוב״ כיצד", d["halacha2"]["explanation"])

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
        bad = ANSWER_ABBREV.replace('"connection": "', '"connection": "he said "yes" then ')
        with self.assertRaises(json.JSONDecodeError):
            E._answer_json(reply(bad), empty_ok=True)


class Engine(Base):
    def test_8_llm_json_reads_the_first_answer_without_a_reask(self):
        self.answers(ANSWER_ABBREV)
        d = E.llm_json(object(), lambda m: E._answer_json(m, empty_ok=True), model="m", max_tokens=5, temperature=0, system="s",
                       messages=[{"role": "user", "content": "q"}])
        self.assertEqual(set(d), KEYS)
        self.assertEqual(self.calls, 1)

    def test_9_two_unparseable_answers_raise_and_the_raw_answer_is_printed(self):
        bad = ANSWER_ABBREV.replace('"connection": "', '"connection": "he said "yes" then ')
        self.answers(bad, bad)
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(json.JSONDecodeError):
            E.llm_json(object(), lambda m: E._answer_json(m, empty_ok=True), model="m", max_tokens=5, temperature=0, system="s",
                       messages=[{"role": "user", "content": "q"}])
        self.assertEqual(self.calls, 2)
        self.assertIn("UNPARSEABLE_MODEL_JSON (first 800 chars): {", out.getvalue())
        self.assertIn('he said "yes"', out.getvalue())

    def test_10_benishchai_agent_saves_a_complete_shoftim_record(self):
        E.sef_json = lambda url, **kw: {"he": [SOURCE]}
        self.answers(ANSWER_TITLE)
        rec = E.benishchai_agent(datetime.date(2026, 8, 9), "שופטים")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["ref"], "Ben Ish Hai, Halachot 1st Year, Shoftim")
        saved = json.load(open(os.path.join(self.tmp, E.BIC_OUT), encoding="utf-8"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["date"], "2026-08-09")
        self.assertIsNone(E.incomplete_content(saved[0]))
        self.assertEqual(E.FAILURES, [])


if __name__ == "__main__":
    unittest.main()
