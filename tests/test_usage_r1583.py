"""R1583: every model call's measured token usage is printed and summed (USAGE), and the run summary gives tokens in/out and dollars
at $3/$15 per MTok. Offline: fake model, no network, no API key (an in-memory anthropic stand-in is used only if the SDK is absent)."""
import io
import os
import sys
import types
import unittest
from contextlib import redirect_stdout

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
try:
    import anthropic  # noqa: F401
except ImportError:            # only so the tests run without the SDK; CI installs the pinned one
    sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=lambda **kw: None, __version__="test-stub")
import engine as E  # noqa: E402


def reply(text, tin=None, tout=None):
    usage = None if tin is None else types.SimpleNamespace(input_tokens=tin, output_tokens=tout)
    return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)], usage=usage)


class Model:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.messages = self

    def create(self, **kw):
        return self.answers.pop(0)


class UsageTests(unittest.TestCase):
    def setUp(self):
        E.USAGE.update(calls=0, input_tokens=0, output_tokens=0)

    def call(self, client):
        out = io.StringIO()
        with redirect_stdout(out):
            msg = E.llm_create(client, model="claude-sonnet-4-6", max_tokens=5, messages=[{"role": "user", "content": "q"}])
        return msg, out.getvalue()

    def test_each_call_is_counted_and_printed(self):
        client = Model(reply("a", 1200, 300), reply("b", 800, 100))
        msg, out = self.call(client)
        self.assertEqual(msg.content[0].text, "a")            # the answer itself is returned unchanged
        self.assertIn("USAGE model=claude-sonnet-4-6 input_tokens=1200 output_tokens=300", out)
        self.call(client)
        self.assertEqual(E.USAGE, {"calls": 2, "input_tokens": 2000, "output_tokens": 400})

    def test_summary_dollars(self):
        self.call(Model(reply("a", 1_000_000, 100_000)))
        s = E.usage_summary()
        self.assertIn("calls=1 input_tokens=1000000 output_tokens=100000", s)
        self.assertIn("usd=4.5000", s)                        # 1 MTok * $3 + 0.1 MTok * $15

    def test_missing_usage_block_counts_the_call_with_zero_tokens(self):
        _, out = self.call(Model(reply("a")))
        self.assertEqual(E.USAGE, {"calls": 1, "input_tokens": 0, "output_tokens": 0})
        self.assertIn("input_tokens=0 output_tokens=0", out)

    def test_failed_call_records_nothing(self):
        class Boom:
            messages = None
            def __init__(self):
                self.messages = self
            def create(self, **kw):
                raise RuntimeError("down")
        n = len(E.FAILURES)
        with redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            E.llm_create(Boom(), model="m", max_tokens=5, messages=[])
        self.assertEqual(E.USAGE["calls"], 0)
        del E.FAILURES[n:]


if __name__ == "__main__":
    unittest.main()
