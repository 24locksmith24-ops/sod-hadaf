"""R1553 (a): the generic archive save and the QA records are complete-or-nothing, per record. Offline: fake model, no network;
writes only in a private temp directory. The shapes are the real ones:
 - david/shlomo/benishchai/breslov parse the model with empty_ok=True, so an unparseable answer became content == {} and was SAVED;
 - 33-36 records in breslov/benishchai/david/shlomo.json are `_error` stubs, some with the placeholder 'השיעור ייכתב בריצה הבאה.';
 - answer_question() saved {} (empty_ok) or the stub 'התשובה תיווצר שוב בקרוב.' to qa.json.
Reads (never writes) the repo's own archive files to prove no complete historical record is refused by the new rule."""
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

HE = "מזמור לדוד יהוה רעי לא אחסר בנאות דשא ירביצני"
DAVID_FULL = {"title": "תהילים — פרק כג", "key_verse": "יהוה רעי", "david_voice": "בטחון", "blessing": "ברכה", "advice": "עצה"}


def reply(text):
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)])


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = {n: getattr(E, n) for n in ("get_text", "llm_create", "anthropic")}
        self.saved_get = requests.get

        def no_network(*a, **kw):
            raise AssertionError("a test reached the network: %r" % (a,))
        requests.get = no_network
        E.FAILURES.clear()
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="r1553_")
        os.chdir(self.tmp)
        E.anthropic = types.SimpleNamespace(Anthropic=lambda **kw: object())

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(E, n, v)
        requests.get = self.saved_get
        E.FAILURES.clear()
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def written(self, name):
        return os.path.exists(os.path.join(self.tmp, name))

    def answers(self, *texts):
        q = list(texts)
        E.llm_create = lambda client, **kw: reply(q.pop(0))


class ArchiveSave(Base):
    def rec(self, content):
        return {"date": "2026-10-30", "ts": "2026-10-30 06:00", "content": content}

    def test_complete_record_is_saved(self):
        self.assertTrue(E._save_archive("david.json", self.rec(DAVID_FULL)))
        self.assertEqual(json.load(open("david.json", encoding="utf-8"))[0]["content"], DAVID_FULL)
        self.assertEqual(E.FAILURES, [])

    def test_empty_dict_content_is_refused_and_not_written(self):
        self.assertFalse(E._save_archive("david.json", self.rec({})))
        self.assertFalse(self.written("david.json"))
        self.assertTrue(any("empty content" in f["error"] for f in E.FAILURES))

    def test_stub_blank_field_and_placeholder_are_refused(self):
        for c in ({"title": "x", "_error": "boom"},
                  dict(DAVID_FULL, advice="   "),
                  dict(DAVID_FULL, key_verse=[]),
                  {"title": "ליקוטי מוהר\"ן", "torah_summary": "השיעור ייכתב בריצה הבאה."}):       # the real placeholder
            self.assertFalse(E._save_archive("breslov.json", self.rec(c)), c)
        self.assertFalse(self.written("breslov.json"))
        self.assertEqual(len(E.FAILURES), 4)

    def test_a_refused_record_never_replaces_the_days_good_one(self):
        self.assertTrue(E._save_archive("david.json", self.rec(DAVID_FULL)))
        self.assertFalse(E._save_archive("david.json", self.rec({})))
        data = json.load(open("david.json", encoding="utf-8"))
        self.assertEqual([r["content"] for r in data], [DAVID_FULL])

    def test_the_hilulot_no_tzaddik_record_is_complete(self):
        r = {"date": "2026-10-30", "ts": "t", "tzaddikim": [], "content": {"intro": "אין הילולא של צדיק מהמאגר ליום זה."}}
        self.assertTrue(E._save_archive("hilulot.json", r))

    def test_no_complete_historical_record_is_refused(self):
        for f in ("breslov.json", "benishchai.json", "david.json", "shlomo.json", "shnayim.json", "hilulot.json"):
            data = json.load(open(os.path.join(ROOT, f), encoding="utf-8"))
            old_kept = [r for r in data if not (r.get("content") or {}).get("_error")]       # what the old guard let through
            refused = [E.incomplete_content(r) for r in old_kept if E.incomplete_content(r)]
            self.assertEqual(refused, [], f)


class AgentUsesTheResult(Base):
    def setUp(self):
        super().setUp()
        E.get_text = lambda ref: {"ref": ref, "he_ref": ref, "hebrew": HE, "segments": [HE]}

    def test_unparseable_answer_becomes_no_record_not_an_empty_one(self):
        self.answers("this is not json at all", "still not json")                    # empty_ok -> {} (the real shape)
        self.assertIsNone(E.david_agent(E.datetime.date(2026, 10, 30)))
        self.assertFalse(self.written("david.json"))
        self.assertTrue(E.FAILURES)

    def test_complete_answer_is_saved(self):
        self.answers(json.dumps(DAVID_FULL, ensure_ascii=False))
        rec = E.david_agent(E.datetime.date(2026, 10, 30))
        self.assertEqual(rec["content"], DAVID_FULL)
        self.assertTrue(self.written("david.json"))


class QA(Base):
    FULL = {"title": "ת", "question": "ש", "intro": "פ", "sources": [{"name": "גמרא", "point": "נ"}], "discussion": "ד",
            "deep_dive": "ע", "conclusion": "ס", "practical": "מ", "disclaimer": "חומר לימוד בלבד"}
    SHORT = {"title": "ת", "summary": "ס", "key_sources": ["גמרא"], "bottom_line": "ש", "disclaimer": "חומר לימוד בלבד"}

    def test_complete_answers_are_saved(self):
        self.assertTrue(E.save_qa({"question": "q", "mode": "full", "lesson": self.FULL}))
        self.assertTrue(E.save_qa({"question": "q", "mode": "short", "lesson": self.SHORT}))
        self.assertEqual(len(json.load(open("qa.json", encoding="utf-8"))), 2)

    def test_empty_stub_partial_and_placeholder_answers_are_refused(self):
        for mode, L in (("full", {}),
                        ("full", {"title": "q", "intro": "התשובה תיווצר שוב בקרוב.", "_error": "boom"}),
                        ("full", dict(self.FULL, sources=[])),
                        ("short", dict(self.SHORT, summary="")),
                        ("full", dict(self.FULL, intro="התשובה תיווצר שוב בקרוב."))):
            self.assertFalse(E.save_qa({"question": "q", "mode": mode, "lesson": L}), (mode, L))
        self.assertFalse(self.written("qa.json"))
        self.assertEqual(len(E.FAILURES), 5)

    def test_answer_question_with_an_unparseable_answer_saves_nothing(self):
        self.answers("not json", "not json either")
        E.answer_question("מה דין ברכת המזון?", "full")
        self.assertFalse(self.written("qa.json"))
        self.assertTrue(any(f["where"] == "qa answer" for f in E.FAILURES))


if __name__ == "__main__":
    unittest.main()
