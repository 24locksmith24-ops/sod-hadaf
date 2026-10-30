"""R1533: on an unparseable model answer the engine asks ONCE more ("Return ONLY valid JSON"); a second failure is the same
failure as before. Offline: fake model, no network, no API key (an in-memory anthropic stand-in is used only if the SDK is absent)."""
import json
import os
import sys
import types
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
try:
    import anthropic  # noqa: F401
except ImportError:            # only so the tests run without the SDK; CI installs the pinned one
    sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=lambda **kw: None, __version__="test-stub")
import engine as E  # noqa: E402
import sections as SEC  # noqa: E402

GOOD = '{"theme":"ת","method":"מ","aramaic":"","links":[],"thread":"ח"}'
BAD = '{"theme":"ר"ל"}'          # the logged shape: a quote inside a Hebrew abbreviation -> JSONDecodeError


def reply(text):
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)])


class Model:
    """client.messages.create(**kw) returning scripted answers in order; records every request"""
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return reply(self.answers.pop(0))


def ask(client, parse=E._answer_json):
    return E.llm_json(client, parse, model="m", max_tokens=5, temperature=0, system="s",
                      messages=[{"role": "user", "content": "q"}])


class Helper(unittest.TestCase):
    def setUp(self):
        E.FAILURES.clear()

    def test_invalid_then_valid_uses_the_valid_answer_in_exactly_two_calls(self):
        m = Model(BAD, GOOD)
        self.assertEqual(ask(m)["thread"], "ח")
        self.assertEqual(len(m.calls), 2)
        self.assertEqual(m.calls[0]["messages"][-1]["content"], "q")
        self.assertEqual(m.calls[1]["messages"][-1]["content"], "q\n\nReturn ONLY valid JSON")
        self.assertEqual({k: v for k, v in m.calls[1].items() if k != "messages"}, {k: v for k, v in m.calls[0].items() if k != "messages"})

    def test_invalid_twice_is_the_same_failure_as_before_after_exactly_two_calls(self):
        m = Model(BAD, BAD)
        with self.assertRaises(json.JSONDecodeError):
            ask(m)
        self.assertEqual(len(m.calls), 2)

    def test_valid_first_answer_makes_one_call(self):
        m = Model(GOOD)
        ask(m)
        self.assertEqual(len(m.calls), 1)

    def test_existing_empty_fallback_is_unchanged_and_not_re_asked(self):     # side agents: no braces -> {} as before
        m = Model("no json at all")
        self.assertEqual(ask(m, lambda r: E._answer_json(r, empty_ok=True)), {})
        self.assertEqual(len(m.calls), 1)


class Connector(unittest.TestCase):
    def setUp(self):
        E.FAILURES.clear()
        self.real = E.anthropic

    def tearDown(self):
        E.anthropic = self.real
        E.FAILURES.clear()

    def run_with(self, *answers):
        m = Model(*answers)
        E.anthropic = types.SimpleNamespace(Anthropic=lambda: m)
        out = E.connector_agent({"he_ref": "x"}, [], [], None, [], [], [], [{"label": "L", "points": "p"}])
        return out, m

    def test_invalid_then_valid(self):
        out, m = self.run_with(BAD, GOOD)
        self.assertEqual(out["thread"], "ח")
        self.assertEqual(len(m.calls), 2)
        self.assertEqual(E.FAILURES, [])

    def test_invalid_twice_takes_the_existing_failure_path(self):
        out, m = self.run_with(BAD, BAD)
        self.assertEqual(out, {"theme": "", "method": "", "aramaic": "", "links": [], "thread": ""})
        self.assertEqual(len(m.calls), 2)
        self.assertEqual(E.FAILURES[0]["where"], "connector_agent")


class Sections(unittest.TestCase):
    NOTES = json.dumps({"notes": [{"label": "A", "points": "נקודה"}]}, ensure_ascii=False)

    def test_invalid_then_valid_second_attempt_asks_for_json_only(self):
        m = Model('{"notes":[{"label":"A","points":"ר"ל"}]}', self.NOTES)
        self.assertEqual(SEC.read_batch(m.create, "m", [("A", "x")]), {"A": "נקודה"})
        self.assertEqual(len(m.calls), 2)
        self.assertTrue(m.calls[1]["messages"][-1]["content"].endswith("\n\nReturn ONLY valid JSON"))
        self.assertFalse(m.calls[0]["messages"][-1]["content"].endswith("Return ONLY valid JSON"))

    def test_invalid_twice_is_the_same_failure_as_before(self):
        m = Model(BAD, BAD)
        with self.assertRaises(RuntimeError) as cm:
            SEC.read_batch(m.create, "m", [("A", "x")])
        self.assertIn("section reading failed after 2 attempts (unparseable answer:", str(cm.exception))
        self.assertEqual(len(m.calls), 2)


if __name__ == "__main__":
    unittest.main()
