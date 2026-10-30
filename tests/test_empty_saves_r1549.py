"""R1549: no empty save is ever written; a failure = no write + the run marked failed. Offline: fake Sefaria answers and a fake model,
no network, no API key; every file write happens in a private temp directory. The failure shapes are the real ones:
 - 2026-10-22 (runs 39721346782, 39792691161): Sefaria /links answered an EMPTY body; .json() raised
   "Expecting value: line 1 column 1 (char 0)" inside get_commentaries and the whole run crashed with a traceback;
 - chok.json: 'מוסר' saved with text '' (e.g. 'שערי תשובה פרק 7': Shaarei Teshuvah has 4 gates), 'משנה' missing from all 87
   records (the calendar title is "Daily Mishnah", the code asked for "Mishnah Yomi"), overview saved as the placeholder
   'תקציר יתווסף בריצה הבאה.' (and, in today's code, as "") when the model failed;
 - yerushalmi.json: summary saved as the placeholder 'תקציר ייווצר בריצה הבאה.' (today's code: "") when the model failed."""
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
try:
    import anthropic  # noqa: F401
except ImportError:            # only so the tests run without the SDK; CI installs the pinned one
    sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=lambda **kw: None, __version__="test-stub")
import engine as E  # noqa: E402

MISSING = object()
HE = "בראשית ברא אלהים את השמים ואת הארץ והארץ היתה תהו ובהו"


class Resp:
    """a requests.Response stand-in with the real failure behaviour: .json() on an empty / HTML body raises requests' JSONDecodeError"""
    def __init__(self, status=200, text=""):
        self.status_code, self.text = status, text

    def json(self):
        try:
            return json.loads(self.text)
        except ValueError as ex:
            raise requests.exceptions.JSONDecodeError(str(ex), self.text, 0)


def reply(text):
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)])


class Base(unittest.TestCase):
    def setUp(self):
        # getattr(..., MISSING): the same tests also run against the unfixed engine (negative control) without crashing in setUp
        self.saved = {n: getattr(E, n, MISSING) for n in ("_sleep", "daily_items", "get_text", "get_commentaries", "sef_json", "llm_create", "anthropic")}
        self.saved_get = requests.get

        def no_network(*a, **kw):
            raise AssertionError("a test reached the network: %r" % (a,))
        requests.get = no_network                   # a test that wants Sefaria answers replaces this; nothing ever goes out
        E._sleep = lambda s: None
        E.FAILURES.clear()
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="r1549_")
        os.chdir(self.tmp)                      # every archive write lands here, never in the repo
        E.anthropic = types.SimpleNamespace(Anthropic=lambda **kw: object())

    def tearDown(self):
        for n, v in self.saved.items():
            if v is MISSING:
                if hasattr(E, n):
                    delattr(E, n)
            else:
                setattr(E, n, v)
        requests.get = self.saved_get
        E.FAILURES.clear()
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def model(self, text=None, exc=None):
        def create(client, **kw):
            if exc:
                E.note_failure("model call", exc)
                raise exc
            return reply(text)
        E.llm_create = create

    def written(self, name):
        return os.path.exists(os.path.join(self.tmp, name))


class SefariaEmptyResponse(Base):
    def test_empty_body_is_retried_then_SefariaUnavailable_not_a_JSONDecodeError(self):
        calls = []
        requests.get = lambda url, timeout=None, params=None: calls.append(url) or Resp(200, "")
        with self.assertRaises(E.SefariaUnavailable):
            E.get_commentaries("Shabbat 2a")              # the 2026-10-22 crash site
        self.assertEqual(len(calls), 4)

    def test_empty_then_good_answer_is_used(self):
        answers = [Resp(200, ""), Resp(200, "<html>busy</html>"), Resp(200, json.dumps({"he": [HE], "ref": "R", "heRef": "ר"}))]
        requests.get = lambda url, timeout=None, params=None: answers.pop(0)
        t = E.get_text("Genesis 1")
        self.assertEqual(t["hebrew"], HE)
        self.assertEqual(answers, [])

    def test_5xx_is_retried_and_a_400_json_error_is_returned_not_raised(self):
        answers = [Resp(503, "{}"), Resp(400, '{"error": "no such ref"}')]
        requests.get = lambda url, timeout=None, params=None: answers.pop(0)
        self.assertEqual(E.sef_json("u"), {"error": "no such ref"})

    def test_daily_run_records_the_failure_and_exits_red_instead_of_crashing(self):
        def main():
            raise E.SefariaUnavailable("https://www.sefaria.org/api/links/Shabbat%202a: Expecting value: line 1 column 1 (char 0)")
        saved_main = E.main
        E.main = main
        try:
            E.run_daily()                                 # must not raise
        finally:
            E.main = saved_main
        self.assertEqual([f["where"] for f in E.FAILURES], ["sefaria"])
        self.assertEqual(E.finish(), 1)

    def test_no_sefaria_get_bypasses_the_helper(self):
        src = open(os.path.join(ROOT, "engine.py"), encoding="utf-8").read()
        self.assertNotIn('requests.get(f"{SEF}', src)
        self.assertEqual(src.count("requests.get("), 3)   # sef_json itself + the two Hebcal calls


class Yerushalmi(Base):
    def setUp(self):
        super().setUp()
        E.daily_items = lambda on=None: {"Yerushalmi Yomi": {"ref": "Jerusalem Talmud Sanhedrin 2:2"}}
        E.get_text = lambda ref: {"ref": ref, "he_ref": "ירושלמי סנהדרין ב ב", "hebrew": HE, "segments": [HE]}
        E.get_commentaries = lambda ref, cap=10: []

    def test_complete_record_is_saved(self):
        self.model("הסוגיה עוסקת בדיני מלך וכהן גדול.")
        rec = E.yerushalmi_agent(E.datetime.date(2026, 10, 30))
        self.assertTrue(rec and rec["summary"])
        self.assertEqual(json.load(open("yerushalmi.json", encoding="utf-8"))[0]["summary"], rec["summary"])
        self.assertEqual(E.FAILURES, [])

    def test_model_failure_saves_nothing_and_is_a_failure(self):
        self.model(exc=RuntimeError("overloaded"))
        self.assertIsNone(E.yerushalmi_agent(E.datetime.date(2026, 10, 30)))
        self.assertFalse(self.written("yerushalmi.json"))
        self.assertTrue(any(f["where"] == "yerushalmi summary" for f in E.FAILURES))

    def test_empty_model_answer_saves_nothing(self):
        self.model("   ")
        self.assertIsNone(E.yerushalmi_agent(E.datetime.date(2026, 10, 30)))
        self.assertFalse(self.written("yerushalmi.json"))
        self.assertTrue(E.FAILURES)

    def test_empty_sefaria_text_saves_nothing(self):
        E.get_text = lambda ref: {"ref": ref, "he_ref": "x", "hebrew": "", "segments": []}
        self.model("never asked")
        self.assertIsNone(E.yerushalmi_agent(E.datetime.date(2026, 10, 30)))
        self.assertFalse(self.written("yerushalmi.json"))
        self.assertTrue(any(f["where"].startswith("yerushalmi text") for f in E.FAILURES))

    def test_no_daf_today_is_not_a_failure(self):
        E.daily_items = lambda on=None: {}
        self.assertIsNone(E.yerushalmi_agent(E.datetime.date(2026, 10, 30)))
        self.assertEqual(E.FAILURES, [])


class Chok(Base):
    def setUp(self):
        super().setUp()
        self.urls = []
        self.empty_for = ()

        def sef(url, timeout=25, params=None, attempts=4):
            self.urls.append(url)
            return {"he": [] if any(s in url for s in self.empty_for) else [HE]}
        E.sef_json = sef
        E.get_text = lambda ref: {"ref": ref, "he_ref": "משנה", "hebrew": HE, "segments": [HE]}
        E.daily_items = lambda on=None: {"Daily Mishnah": {"ref": "Mishnah Oholot 14:1-2"}}   # the real calendar title

    def run_chok(self, day=E.datetime.date(2026, 10, 30)):
        return E.chok_agent(day, "וירא", "Genesis 18:1-22:24", HE, "שבת ב.")

    def test_complete_record_has_all_eight_categories(self):
        self.model("שמונה הקטגוריות מתחברות סביב החסד.")
        rec = self.run_chok()
        self.assertEqual(sorted(rec["sections"]), sorted(E.CHOK_CATEGORIES))
        self.assertTrue(all(v["text"] for v in rec["sections"].values()))
        self.assertEqual(json.load(open("chok.json", encoding="utf-8"))[0]["overview"], rec["overview"])
        self.assertEqual(E.FAILURES, [])

    def test_mishnah_comes_from_the_real_calendar_title(self):
        self.model("ת")
        self.assertIn("משנה", self.run_chok()["sections"])

    def test_old_calendar_key_only_still_works(self):
        E.daily_items = lambda on=None: {"Mishnah Yomi": {"ref": "Mishnah Berakhot 1:1"}}
        self.model("ת")
        self.assertIn("משנה", self.run_chok()["sections"])

    def test_empty_mussar_text_saves_nothing(self):
        self.empty_for = ("Mesillat", "Duties", "Shaarei", "Pirkei")        # the saved shape: 'מוסר' with text ''
        self.model("ת")
        self.assertIsNone(self.run_chok())
        self.assertFalse(self.written("chok.json"))
        self.assertTrue(any("מוסר" in f["error"] for f in E.FAILURES))

    def test_missing_mishnah_saves_nothing(self):
        E.daily_items = lambda on=None: {}                                    # the saved shape: 'משנה' absent
        self.model("ת")
        self.assertIsNone(self.run_chok())
        self.assertFalse(self.written("chok.json"))
        self.assertTrue(any("משנה" in f["error"] for f in E.FAILURES))

    def test_overview_model_failure_saves_nothing(self):
        self.model(exc=RuntimeError("overloaded"))
        self.assertIsNone(self.run_chok())
        self.assertFalse(self.written("chok.json"))

    def test_empty_overview_saves_nothing(self):
        self.model("")
        self.assertIsNone(self.run_chok())
        self.assertFalse(self.written("chok.json"))
        self.assertTrue(any(f["where"] == "chok overview" for f in E.FAILURES))

    def test_every_day_of_the_year_asks_only_for_a_mussar_ref_that_exists(self):
        self.model("ת")
        valid = ([f"Mesillat Yesharim.{c}" for c in range(1, 9)] +
                 [f"Duties of the Heart, {t}.1" for t in E.DUTIES_OF_THE_HEART_TREATISES] +
                 [f"Shaarei Teshuvah.{c}" for c in range(1, 5)] + [f"Pirkei Avot.{c}" for c in range(1, 7)])
        valid_urls = {f"{E.SEF}/texts/{requests.utils.quote(r)}" for r in valid}
        seen = set()
        for doy in range(366):
            self.urls = []
            self.run_chok(E.datetime.date(2028, 1, 1) + E.datetime.timedelta(days=doy))
            mussar = [u for u in self.urls if any(b in u for b in ("Mesillat", "Duties", "Shaarei", "Pirkei"))]
            self.assertEqual(len(mussar), 1)
            self.assertIn(mussar[0], valid_urls)
            seen.add(mussar[0])
        self.assertEqual(seen, valid_urls)            # all 28 are used over a year
        for u in seen:
            self.assertNotRegex(u, r"Shaarei%20Teshuvah\.[5-8]$|Pirkei%20Avot\.[78]$|Duties%20of%20the%20Heart\.\d")

    def test_agent_exception_in_main_is_a_failure_not_a_print(self):
        src = open(os.path.join(ROOT, "engine.py"), encoding="utf-8").read()
        self.assertIn('note_failure("chok", ex)', src)
        self.assertIn('note_failure("yerushalmi", ex)', src)


class SefariaRetryPolicy(Base):
    """Codex PR8 P2-3: retried = timeout / connection error / 5xx / empty / non-JSON; NEVER a 4xx or any other requests error"""

    def test_a_4xx_is_never_retried(self):
        calls = []
        requests.get = lambda url, timeout=None, params=None: calls.append(url) or Resp(404, "")
        with self.assertRaises(E.SefariaUnavailable):
            E.sef_json("u")
        self.assertEqual(len(calls), 1)

    def test_a_4xx_json_error_is_returned_after_one_call(self):
        calls = []
        requests.get = lambda url, timeout=None, params=None: calls.append(url) or Resp(400, '{"error": "Could not find title"}')
        self.assertEqual(E.sef_json("u"), {"error": "Could not find title"})
        self.assertEqual(len(calls), 1)

    def test_a_timeout_is_retried(self):
        answers = [requests.Timeout("read timed out"), requests.ConnectionError("reset"), Resp(200, '{"ok": 1}')]

        def get(url, timeout=None, params=None):
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        requests.get = get
        self.assertEqual(E.sef_json("u"), {"ok": 1})
        self.assertEqual(answers, [])

    def test_another_requests_error_is_not_retried(self):
        calls = []

        def get(url, timeout=None, params=None):
            calls.append(url)
            raise requests.exceptions.InvalidURL("bad url")
        requests.get = get
        with self.assertRaises(E.SefariaUnavailable):
            E.sef_json("u")
        self.assertEqual(len(calls), 1)


class LessonCompleteness(Base):
    """Codex PR8 P1-1: a permanent lesson needs its required fields with real text and no placeholder; length alone is not enough"""

    def lesson(self, **over):
        long = "דברי תורה ארוכים " * 60
        L = {"title": "כותרת", "intro": long, "daf": {"ref": "שבת ב.", "teaching": long}, "mishnayot": [],
             "connections": [{"a": "x", "b": "y", "point": "z"}], "gematria_note": "גימטריה", "question": "שאלה", "takeaway": "לקח"}
        L.update(over)
        return L

    def save(self, lesson):
        import lessons_store as LS
        return LS.save_lesson({"date": "2026-10-30", "ref": "Shabbat 2", "he_ref": "שבת ב", "lesson": lesson}, root=os.path.join(self.tmp, "lessons"))

    def test_complete_lesson_is_saved(self):
        st, path = self.save(self.lesson())
        self.assertEqual(st, "created")
        self.assertTrue(os.path.exists(path))

    def test_long_but_placeholder_lesson_is_refused_and_not_written(self):
        import lessons_store as LS
        L = self.lesson(takeaway="תקציר יתווסף בריצה הבאה.")          # the real placeholder text
        self.assertGreater(len(json.dumps(L, ensure_ascii=False)), 1000)
        with self.assertRaises(LS.StubRefused) as cm:
            self.save(L)
        self.assertIn("placeholder", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "lessons")))

    def test_long_lesson_missing_a_required_field_is_refused(self):
        import lessons_store as LS
        for k in ("title", "intro", "gematria_note", "question", "takeaway"):
            with self.assertRaises(LS.StubRefused, msg=k):
                self.save(self.lesson(**{k: "  "}))
        with self.assertRaises(LS.StubRefused):
            self.save(self.lesson(daf={"ref": "x", "teaching": ""}))
        with self.assertRaises(LS.StubRefused):
            self.save(self.lesson(connections=[]))
        L = self.lesson(); del L["mishnayot"]
        with self.assertRaises(LS.StubRefused):
            self.save(L)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "lessons")))

    def test_every_real_permanent_lesson_is_still_complete(self):
        import glob
        import lessons_store as LS
        files = [f for f in sorted(glob.glob(os.path.join(ROOT, "lessons", "*", "*.json"))) if not f.endswith("index.json")]
        self.assertTrue(files)
        bad = [(f, LS.incomplete_reason(json.load(open(f, encoding="utf-8"))["lesson"])) for f in files]
        self.assertEqual([b for b in bad if b[1]], [])


# the calendar titles Sefaria actually returned on 2026-10-30 (GET /api/calendars), with the two refs this suite needs
REAL_CALENDAR_TITLES = ["Parashat Hashavua", "Haftarah", "Daf Yomi", "929", "Daily Mishnah", "Daily Rambam", "Daily Rambam (3 Chapters)",
                        "Daf a Week", "Halakhah Yomit", "Arukh HaShulchan Yomi", "Tanakh Yomi", "Chok LeYisrael", "Tanya Yomi", "Yerushalmi Yomi"]


class _StopAfterMishnayot(BaseException):
    """BaseException on purpose: main()'s own `except Exception` blocks must not swallow it"""


class MainLessonMishnah(Base):
    """R1552: main()'s lesson asked the calendar for "Mishnah Yomi" (never returned), so every lesson had NO mishnayot."""

    def test_helper_finds_the_real_calendar_title(self):
        items = {t: {"ref": "R:" + t} for t in REAL_CALENDAR_TITLES}
        self.assertNotIn("Mishnah Yomi", items)
        self.assertEqual(E.daily_mishnah_item(items)["ref"], "R:Daily Mishnah")
        self.assertEqual(E.daily_mishnah_item({"Mishnah Yomi": {"ref": "old"}})["ref"], "old")
        self.assertIsNone(E.daily_mishnah_item({}))

    def test_main_fetches_the_days_mishnah_from_the_real_calendar(self):
        fetched = []
        items = {t: {"ref": "R:" + t} for t in REAL_CALENDAR_TITLES}
        items["Daf Yomi"] = {"ref": "Shabbat 2a"}
        items["Daily Mishnah"] = {"ref": "Mishnah Oholot 14:1-2"}
        items["Parashat Hashavua"] = {"ref": "Genesis 18:1-22:24", "displayValue": {"he": "וירא"}}

        def get_text(ref):
            fetched.append(ref)
            if ref == "Genesis 18:1-22:24":            # the first fetch after the mishnayot: stop main() here
                raise _StopAfterMishnayot()
            return {"ref": ref, "he_ref": ref, "hebrew": HE, "segments": [HE, HE + " ב"]}
        saved = {n: getattr(E, n) for n in ("resting_reason", "main")}
        E.resting_reason = lambda d: None
        E.daily_items = lambda on=None: items
        E.get_text = get_text
        E.get_commentaries = lambda ref, cap=10: []
        try:
            with self.assertRaises(_StopAfterMishnayot):
                E.main()
        finally:
            for n, v in saved.items():
                setattr(E, n, v)
        self.assertEqual(fetched, ["Shabbat 2a", "Mishnah Oholot 14:1-2", "Genesis 18:1-22:24"])


if __name__ == "__main__":
    unittest.main()
