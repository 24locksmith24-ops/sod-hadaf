"""Failures are loud: no stub is ever saved, the SDK drift is caught, the process exits non-zero. Offline (a fake client; no network, no API key)."""
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


class Boom:
    class messages:
        @staticmethod
        def create(**kw):
            raise TypeError("Messages.create() got an unexpected keyword argument 'temperature'")


class Fine:
    class messages:
        @staticmethod
        def create(**kw):
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="ok")])


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


class Wrapper(InTmp):
    def test_returns_the_result_and_records_nothing_on_success(self):
        r = E.llm_create(Fine, model="m", max_tokens=5, temperature=0.1, messages=[])
        self.assertEqual(r.content[0].text, "ok")
        self.assertEqual(E.FAILURES, [])

    def test_failure_is_recorded_and_re_raised(self):
        with self.assertRaises(TypeError):
            E.llm_create(Boom, model="m", max_tokens=5, temperature=0.1, messages=[])
        self.assertEqual(len(E.FAILURES), 1)
        self.assertIn("temperature", E.FAILURES[0]["error"])

    def test_preflight_passes_with_the_pinned_sdk(self):
        self.assertTrue(E.preflight_sdk(), E.FAILURES)

    def test_preflight_fails_when_the_sdk_dropped_temperature(self):
        class NoTemp:
            def __init__(self, api_key=None):
                self.messages = types.SimpleNamespace(create=lambda *, model, max_tokens, messages, system=None: None)
        real = E.anthropic
        E.anthropic = types.SimpleNamespace(Anthropic=NoTemp, __version__="1.0.0")
        try:
            self.assertFalse(E.preflight_sdk())
        finally:
            E.anthropic = real
        self.assertIn("1.0.0", E.FAILURES[0]["error"])


class NoStubsSaved(InTmp):
    STUB = {"title": "x", "intro": "השיעור המלא ייכתב בריצה הבאה.", "_error": "boom"}

    def test_daily_lesson_stub_is_not_saved(self):
        self.assertFalse(E.save({"date": "2026-10-21", "ref": "Bekhorot 33", "he_ref": "x", "lesson": self.STUB}))
        self.assertFalse(os.path.exists(E.OUT))
        self.assertEqual(len(E.FAILURES), 1)

    def test_good_lesson_is_saved(self):
        self.assertTrue(E.save({"date": "2026-10-21", "ref": "Bekhorot 33", "he_ref": "x", "lesson": {"title": "t", "intro": "i" * 1200}}))
        self.assertEqual(json.load(open(E.OUT, encoding="utf-8"))[0]["date"], "2026-10-21")
        self.assertEqual(E.FAILURES, [])

    def test_archive_stub_is_not_saved(self):
        self.assertFalse(E._save_archive("breslov.json", {"date": "2026-10-21", "content": self.STUB}))
        self.assertFalse(os.path.exists("breslov.json"))
        self.assertEqual(len(E.FAILURES), 1)

    def test_archive_good_record_is_saved(self):
        self.assertTrue(E._save_archive("breslov.json", {"date": "2026-10-21", "content": {"title": "t"}}))
        self.assertTrue(os.path.exists("breslov.json"))

    def test_qa_stub_is_not_saved(self):
        self.assertFalse(E.save_qa({"question": "q", "lesson": self.STUB}))
        self.assertFalse(os.path.exists(E.QA_OUT))


class ExitCode(InTmp):
    def test_clean_run_exits_zero(self):
        self.assertEqual(E.finish(), 0)

    def test_any_failure_exits_non_zero(self):
        E.note_failure("t", "x")
        self.assertEqual(E.finish(), 1)


if __name__ == "__main__":
    unittest.main()
