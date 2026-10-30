"""R1552: the archive backfill regenerates incomplete records COMPLETE for their own date, never deletes, keeps the old record when the
regeneration is not complete, and counts before/after. Offline: fake agents / fake calendar, no network, no model; every file lives in a
private temp dir (fixture archives copied from the real shapes). Also: an old date never becomes data[0]; the agents take that date's
calendar (items=); backfill_lessons uses the "Daily Mishnah" title."""
import datetime
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
import backfill_records as BR  # noqa: E402
import backfill_lessons as BL  # noqa: E402

GOOD = {"title": "תהילים — פרק כג", "key_verse": "יהוה רעי", "david_voice": "בטחון", "blessing": "ברכה", "advice": "עצה"}
HE = "מזמור לדוד יהוה רעי לא אחסר"


class Base(unittest.TestCase):
    def setUp(self):
        self.saved_E = {n: getattr(E, n) for n in ("daily_items", "get_text", "preflight_sdk", "sef_json", "llm_create", "anthropic")}
        self.saved_arch = dict(BR.ARCHIVES)
        self.saved_get = requests.get

        def no_network(*a, **kw):
            raise AssertionError("a test reached the network: %r" % (a,))
        requests.get = no_network
        E.FAILURES.clear()
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="r1552_")
        os.chdir(self.tmp)
        E.preflight_sdk = lambda: True
        E.daily_items = lambda on=None: {"Daf Yomi": {"ref": "Shabbat 2a"}}
        # the real shape: one complete record, one _error stub with the real placeholder, newest first
        self.david = [{"date": "2026-10-29", "ts": "t", "psalm_no": 23, "content": GOOD},
                      {"date": "2026-10-20", "ts": "t", "psalm_no": 14, "content": {"title": "תהילים פרק 14", "_error": "Messages.create() got an unexpected keyword argument 'temperature'"}},
                      {"date": "2026-10-19", "ts": "t", "psalm_no": 13, "content": {"title": "ליקוטי", "torah_summary": "השיעור ייכתב בריצה הבאה."}}]
        json.dump(self.david, open("david.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    def tearDown(self):
        for n, v in self.saved_E.items():
            setattr(E, n, v)
        BR.ARCHIVES.clear(); BR.ARCHIVES.update(self.saved_arch)
        requests.get = self.saved_get
        E.FAILURES.clear()
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def regen(self, complete=True):
        calls = []

        def agent(d, items):
            calls.append((d.isoformat(), items))
            c = dict(GOOD, title="תהילים %s" % d.isoformat()) if complete else {}
            rec = {"date": d.isoformat(), "ts": "t", "content": c}
            return rec if E._save_archive("david.json", rec) else None
        BR.ARCHIVES.clear()
        BR.ARCHIVES["david"] = ("david.json", E.incomplete_content, agent)
        return calls


class DryRun(Base):
    def test_dry_run_counts_and_writes_nothing(self):
        calls = self.regen()
        before = open("david.json", "rb").read()
        self.assertEqual(BR.main([]), 0)
        self.assertEqual(open("david.json", "rb").read(), before)
        self.assertEqual(calls, [])
        self.assertEqual(BR.counts(BR.incomplete(["david"])), {"david": 2})

    def test_the_real_archives_classify_without_network(self):
        BR.ARCHIVES.clear(); BR.ARCHIVES.update(self.saved_arch)
        os.chdir(ROOT)
        try:
            table = BR.incomplete(list(BR.ARCHIVES))          # read-only over the repo's own files
        finally:
            os.chdir(self.tmp)
        self.assertEqual(set(table), set(BR.ARCHIVES))
        for n, rows in table.items():
            for d, why in rows:
                self.assertRegex(d, r"^\d{4}-\d{2}-\d{2}$")
                self.assertTrue(why)


class Apply(Base):
    def test_incomplete_records_are_replaced_complete_for_their_own_date(self):
        calls = self.regen()
        self.assertEqual(BR.main(["--apply", "--limit", "10"]), 0)
        self.assertEqual([c[0] for c in calls], ["2026-10-19", "2026-10-20"])           # oldest first
        data = json.load(open("david.json", encoding="utf-8"))
        self.assertEqual([r["date"] for r in data], ["2026-10-29", "2026-10-20", "2026-10-19"])   # newest first, nothing deleted
        self.assertEqual(data[0]["content"], GOOD)                                       # the good one untouched
        self.assertEqual(BR.counts(BR.incomplete(["david"])), {"david": 0})

    def test_an_incomplete_regeneration_keeps_the_old_record_and_fails_loud(self):
        self.regen(complete=False)
        before = json.load(open("david.json", encoding="utf-8"))
        self.assertEqual(BR.main(["--apply"]), 1)
        self.assertEqual(json.load(open("david.json", encoding="utf-8")), before)          # never deleted, never emptied
        self.assertTrue(E.FAILURES)

    def test_limit_and_date_range(self):
        calls = self.regen()
        self.assertEqual(BR.main(["--apply", "--limit", "1"]), 0)
        self.assertEqual([c[0] for c in calls], ["2026-10-19"])
        calls.clear()
        self.assertEqual(BR.main(["--apply", "--from", "2026-10-20", "--to", "2026-10-20"]), 0)
        self.assertEqual([c[0] for c in calls], ["2026-10-20"])

    def test_the_agent_gets_that_dates_calendar(self):
        calls = self.regen()
        seen = []
        E.daily_items = lambda on=None: seen.append(on) or {"tag": str(on)}
        BR.main(["--apply"])
        self.assertEqual(seen, [datetime.date(2026, 10, 19), datetime.date(2026, 10, 20)])
        self.assertEqual([c[1] for c in calls], [{"tag": "2026-10-19"}, {"tag": "2026-10-20"}])

    def test_an_agent_exception_keeps_the_old_record(self):
        def boom(d, items):
            raise E.SefariaUnavailable("empty body")
        BR.ARCHIVES.clear()
        BR.ARCHIVES["david"] = ("david.json", E.incomplete_content, boom)
        before = open("david.json", "rb").read()
        self.assertEqual(BR.main(["--apply"]), 1)
        self.assertEqual(open("david.json", "rb").read(), before)

    def test_unknown_archive_is_refused(self):
        self.assertEqual(BR.main(["--archives", "nope"]), 2)


class EngineSupport(Base):
    def test_an_old_date_never_becomes_the_first_entry(self):
        self.assertTrue(E._save_archive("david.json", {"date": "2026-01-01", "ts": "t", "content": GOOD}))
        self.assertEqual(json.load(open("david.json", encoding="utf-8"))[0]["date"], "2026-10-29")

    def test_yerushalmi_uses_the_given_calendar(self):
        E.daily_items = lambda on=None: (_ for _ in ()).throw(AssertionError("must not read today's calendar"))
        E.get_text = lambda ref: {"ref": ref, "he_ref": "ירושלמי", "hebrew": "", "segments": []}
        self.assertIsNone(E.yerushalmi_agent(datetime.date(2026, 10, 19), items={"Yerushalmi Yomi": {"ref": "Jerusalem Talmud Berakhot 1:1"}}))
        self.assertTrue(any(f["where"].startswith("yerushalmi text Jerusalem Talmud Berakhot 1:1") for f in E.FAILURES))

    def test_chok_uses_the_given_calendar_for_the_mishnah(self):
        E.daily_items = lambda on=None: (_ for _ in ()).throw(AssertionError("must not read today's calendar"))
        asked = []
        E.sef_json = lambda url, timeout=25, params=None, attempts=4: {"he": [HE]}
        E.get_text = lambda ref: asked.append(ref) or {"ref": ref, "he_ref": ref, "hebrew": HE, "segments": [HE]}
        E.anthropic = types.SimpleNamespace(Anthropic=lambda **kw: object())
        E.llm_create = lambda client, **kw: types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="חוט מקשר")])
        rec = E.chok_agent(datetime.date(2026, 10, 19), "וירא", "Genesis 18:1", HE, "שבת ב.", items={"Daily Mishnah": {"ref": "Mishnah Peah 1:1"}})
        self.assertIn("Mishnah Peah 1:1", asked)
        self.assertEqual(sorted(rec["sections"]), sorted(E.CHOK_CATEGORIES))

    def test_backfill_lessons_asks_for_the_daily_mishnah(self):
        asked = []
        E.get_text = lambda ref: asked.append(ref) or {"ref": ref, "he_ref": ref, "hebrew": HE, "segments": [HE]}
        saved_c = BL.E.get_commentaries
        BL.E.get_commentaries = lambda ref, cap=10: []
        try:
            prep = BL.prepare(datetime.date(2026, 10, 19), {"Daf Yomi": {"ref": "Shabbat 2a"}, "Daily Mishnah": {"ref": "Mishnah Peah 1:1"}}, HE)
        finally:
            BL.E.get_commentaries = saved_c
        self.assertIn("Mishnah Peah 1:1", asked)
        self.assertTrue(prep["mishnayot"])


if __name__ == "__main__":
    unittest.main()
