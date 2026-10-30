"""R1526: one tolerant parser + one re-ask for every model answer; still fail-loud when the answer stays unusable.
Fixtures (tests/fixtures/model_json_failures.json) make plain json.loads raise EXACTLY the error logged by runs #312, #314,
#319 and #320 (same line, column, char), plus the empty answer. Offline: fake model, no network, no API key."""
import json
import os
import shutil
import sys
import types
import unittest
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _anthropic_stub  # noqa: E402
_anthropic_stub.install()                  # offline stand-in only when the real SDK is not installed
import model_json as MJ  # noqa: E402
import sections as SEC  # noqa: E402
import engine as E  # noqa: E402

FIX = json.load(open(os.path.join(ROOT, "tests", "fixtures", "model_json_failures.json"), encoding="utf-8"))
BY_RUN = {str(c["run"]): c for c in FIX}
GOOD = '{"theme":"ת","method":"מ","aramaic":"","links":[{"a":"א","b":"ב","point":"נ"}],"thread":"ח"}'


def reply(text):
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)])


class Scripted:
    """create(**kw) that returns the scripted answers in order and records every request"""
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, **kw):
        self.calls.append(kw)
        return reply(self.answers.pop(0))


class TheLoggedFailures(unittest.TestCase):
    def test_each_fixture_reproduces_the_logged_error(self):
        for c in FIX:
            with self.subTest(run=c["run"]):
                with self.assertRaises(ValueError) as cm:
                    json.loads(c["raw"])
                self.assertEqual(str(cm.exception), c["json_loads_error"])
                self.assertIn(c["json_loads_error"], c["log_line"])

    def test_read_in_sections_answers_are_repaired_with_every_note(self):       # #314, #319: stray quote in ר"ל
        for run in ("314", "319"):
            with self.subTest(run=run):
                notes = MJ.parse_model_json(BY_RUN[run]["raw"])["notes"]
                got = {n["label"]: n["points"] for n in notes}
                self.assertIn('ר"ל', got["L"])
                self.assertEqual(len(notes), BY_RUN[run]["raw"].count('"label"'))

    def test_connector_stray_quote_is_repaired(self):                            # #312: שו"ע inside a string
        out = MJ.parse_model_json(BY_RUN["312"]["raw"])
        self.assertIn('שו"ע', out["links"][-1]["point"])
        self.assertEqual(out["thread"], "הכל מתחבר.")

    def test_connector_cut_off_answer_is_refused_not_trimmed(self):            # #320: cut off at max_tokens
        with self.assertRaises(MJ.ModelJSONError) as cm:
            MJ.parse_model_json(BY_RUN["320"]["raw"])
        self.assertIn("cut off", str(cm.exception))

    def test_cut_off_answer_is_re_asked_and_the_full_answer_is_used(self):
        create = Scripted(BY_RUN["320"]["raw"], GOOD)
        out = MJ.ask_json(create, model="m", max_tokens=5, messages=[{"role": "user", "content": "q"}])
        self.assertEqual(out["thread"], "ח")
        self.assertEqual(len(create.calls), 2)

    def test_empty_answer_is_not_parsed(self):                                   # #306/#307 shape
        with self.assertRaises(MJ.ModelJSONError):
            MJ.parse_model_json(BY_RUN["306/307"]["raw"])


class Parser(unittest.TestCase):
    def test_fences_and_prose_around_the_object(self):
        self.assertEqual(MJ.parse_model_json('Here it is:\n```json\n{"a": [1, 2]}\n```\nThanks')["a"], [1, 2])

    def test_missing_commas_are_inserted(self):
        self.assertEqual(MJ.parse_model_json('{"a":1 "b":2}'), {"a": 1, "b": 2})
        self.assertEqual(MJ.parse_model_json('{"a":"x" "b":"y"}'), {"a": "x", "b": "y"})
        self.assertEqual(MJ.parse_model_json('[{"a":1} {"b":2}]'), [{"a": 1}, {"b": 2}])
        self.assertEqual(MJ.parse_model_json('{"a":[1 2], "b":true "c":null}'), {"a": [1, 2], "b": True, "c": None})

    def test_more_than_one_json_value_is_refused(self):
        for bad in ('prefix {"chosen":1} prose {"other":2}', '{"a":1}{"b":2}', '[1] and [2]', '{"a":"x"} see [note]'):
            with self.subTest(bad=bad):
                with self.assertRaises(MJ.ModelJSONError) as cm:
                    MJ.parse_model_json(bad)
                self.assertIn("more than one JSON value", str(cm.exception))

    def test_cut_off_anywhere_is_refused(self):
        for bad in ('{"a":"complete","b":"cut', '{"a":"complete","b":', '{"a":[1,2', '{"notes":[{"label":"x","points":"p"}'):
            with self.subTest(bad=bad):
                with self.assertRaises(MJ.ModelJSONError) as cm:
                    MJ.parse_model_json(bad)
                self.assertIn("cut off", str(cm.exception))

    def test_trailing_commas(self):
        self.assertEqual(MJ.parse_model_json('{"a": [1, 2,], "b": {"c": "d",},}'), {"a": [1, 2], "b": {"c": "d"}})

    def test_valid_json_is_untouched(self):
        self.assertEqual(MJ.parse_model_json(GOOD), json.loads(GOOD))

    def test_garbage_stays_a_failure(self):
        for bad in ("I cannot help with that.", "{", '{"notes":[{"lab', "   ", "```json\n```"):
            with self.subTest(bad=bad):
                with self.assertRaises(MJ.ModelJSONError):
                    MJ.parse_model_json(bad)


class ReAsk(unittest.TestCase):
    def test_one_re_ask_after_an_empty_answer(self):
        create = Scripted("", GOOD)
        out = MJ.ask_json(create, model="m", max_tokens=5, messages=[{"role": "user", "content": "q"}])
        self.assertEqual(out["theme"], "ת")
        self.assertEqual(len(create.calls), 2)
        self.assertTrue(create.calls[1]["messages"][-1]["content"].endswith(MJ.REASK))
        self.assertEqual(create.calls[0]["messages"][-1]["content"], "q")       # the caller's messages are not mutated

    def test_garbage_twice_raises_after_exactly_two_calls(self):
        create = Scripted("no json here", "still prose")
        with self.assertRaises(MJ.ModelJSONError):
            MJ.ask_json(create, model="m", max_tokens=5, messages=[{"role": "user", "content": "q"}])
        self.assertEqual(len(create.calls), 2)

    def test_a_repairable_answer_needs_no_re_ask(self):
        create = Scripted(BY_RUN["312"]["raw"])
        MJ.ask_json(create, model="m", max_tokens=5, messages=[{"role": "user", "content": "q"}])
        self.assertEqual(len(create.calls), 1)


class Sections(unittest.TestCase):
    def batch_for(self, raw):
        return [(n["label"], "טקסט") for n in MJ.parse_model_json(raw)["notes"]]

    def test_logged_answer_is_read_in_one_call(self):
        create = Scripted(BY_RUN["314"]["raw"])
        notes = SEC.read_batch(create, "m", self.batch_for(BY_RUN["314"]["raw"]))
        self.assertIn('ר"ל', notes["L"])
        self.assertEqual(len(create.calls), 1)

    def test_second_attempt_asks_for_json_only(self):
        batch = self.batch_for(BY_RUN["314"]["raw"])
        create = Scripted("garbage", BY_RUN["314"]["raw"])
        SEC.read_batch(create, "m", batch)
        self.assertNotIn(MJ.REASK, create.calls[0]["messages"][-1]["content"])
        self.assertTrue(create.calls[1]["messages"][-1]["content"].endswith(MJ.REASK))

    def test_a_cut_off_answer_fails_the_batch_after_the_re_ask(self):
        cut = '{"notes":[{"label":"A","points":"א"},{"label":"B","po'           # cut off: refused, never trimmed to A only
        create = Scripted(cut, cut)
        with self.assertRaises(RuntimeError) as cm:
            SEC.read_batch(create, "m", [("A", "x"), ("B", "y")])
        self.assertIn("cut off", str(cm.exception))
        self.assertTrue(create.calls[1]["messages"][-1]["content"].endswith(MJ.REASK))

    def test_garbage_twice_still_fails_the_batch(self):
        with self.assertRaises(RuntimeError) as cm:
            SEC.read_batch(Scripted("garbage", ""), "m", [("A", "x")])
        self.assertIn("unparseable answer", str(cm.exception))


class Agents(unittest.TestCase):
    def setUp(self):
        E.FAILURES.clear()
        self.real = E.anthropic

    def tearDown(self):
        E.anthropic = self.real
        E.FAILURES.clear()

    def fake(self, *answers):
        create = Scripted(*answers)
        E.anthropic = types.SimpleNamespace(Anthropic=lambda: types.SimpleNamespace(messages=types.SimpleNamespace(create=create)))
        return create

    def conn(self):
        return E.connector_agent({"he_ref": "x"}, [], [], None, [], [], [], [{"label": "L", "points": "p"}])

    def test_connector_recovers_the_logged_312_answer(self):
        self.fake(BY_RUN["312"]["raw"])
        self.assertIn('שו"ע', self.conn()["links"][-1]["point"])
        self.assertEqual(E.FAILURES, [])

    def test_connector_garbage_twice_is_a_loud_failure(self):
        create = self.fake("prose", "")
        out = self.conn()
        self.assertEqual(out["links"], [])
        self.assertTrue(out["_error"])
        self.assertEqual(len(create.calls), 2)
        self.assertEqual(E.FAILURES[0]["where"], "connector_agent")

    def test_a_failed_connector_blocks_the_lesson_and_its_save(self):          # Codex REV1 P1
        notes = json.dumps({"notes": [{"label": "Bekhorot 33", "points": "נקודה"}]}, ensure_ascii=False)
        create = self.fake(notes, "prose", "")          # sections OK, connector unparseable twice, nothing left for write_lesson
        real_units = E.SEC.make_units
        E.SEC.make_units = lambda daf, mishnayot, comms: [("Bekhorot 33", "טקסט")]
        try:
            lesson, conn = E.generate_lesson({"he_ref": "בכורות לג", "ref": "Bekhorot 33"}, [], [], None, [], [], {}, [], [])
        finally:
            E.SEC.make_units = real_units
        self.assertIn("connector_agent", lesson["_error"])
        self.assertEqual(len(create.calls), 3)          # write_lesson was never called
        self.assertFalse(E.save({"date": "2026-10-30", "ref": "Bekhorot 33", "he_ref": "x", "lesson": lesson}))

    def test_write_lesson_garbage_raises_for_the_caller(self):
        self.fake("prose", "more prose")
        with self.assertRaises(MJ.ModelJSONError):
            E.write_lesson({"he_ref": "x"}, [], [], None, [], [], {}, {}, [], [{"label": "L", "points": "p"}])


class InTmp(unittest.TestCase):
    def setUp(self):
        E.FAILURES.clear()
        self.old = os.getcwd()
        self.d = os.path.join(ROOT, "tests", "_tmp_" + uuid.uuid4().hex[:8])
        os.makedirs(self.d)
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.old)
        shutil.rmtree(self.d, ignore_errors=True)
        E.FAILURES.clear()

    def test_a_side_agent_answer_that_stays_unparseable_is_not_archived(self):
        real = E.anthropic
        create = Scripted("prose", "")
        E.anthropic = types.SimpleNamespace(Anthropic=lambda: types.SimpleNamespace(messages=types.SimpleNamespace(create=create)))
        try:
            try:
                content = MJ.ask_json(lambda **kw: E.llm_create(E.anthropic.Anthropic(), **kw), model="m", max_tokens=5,
                                      messages=[{"role": "user", "content": "q"}])
            except Exception as ex:      # the agents' own except branch builds exactly this
                content = {"title": "t", "_error": str(ex)[:200]}
            self.assertFalse(E._save_archive("david.json", {"date": "2026-10-30", "content": content}))
            self.assertFalse(os.path.exists("david.json"))
            self.assertEqual(E.FAILURES[0]["where"], "archive david.json")
        finally:
            E.anthropic = real


if __name__ == "__main__":
    unittest.main()
