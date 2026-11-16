"""R2059 F1: sections._parse repairs the two measured failure shapes, only after a failed parse; read_batch logs the raw answer.
Offline: no model call, no network. Goes into the repo as tests/test_sections_parse_r2059.py."""
import json, os, sys, types, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sections as SEC

LAB = "הדף חולין ל״ב"


def answer(points):
    return '{"notes":[{"label":"%s","points":"%s"}]}' % (LAB, points)


class Parse(unittest.TestCase):
    def test_valid_untouched(self):
        raw = answer('כך פסק הרמב\\"ם')          # a correctly escaped quote stays a quote
        self.assertEqual(SEC._parse(raw)["notes"][0]["points"], 'כך פסק הרמב"ם')

    def test_bare_hebrew_abbreviation_quote(self):      # "Expecting ',' delimiter"
        raw = answer('כך פסק הרמב"ם ורש"י')
        with self.assertRaises(json.JSONDecodeError):
            json.loads(raw)
        self.assertEqual(SEC._parse(raw)["notes"][0]["points"], "כך פסק הרמב״ם ורש״י")

    def test_invalid_escape(self):      # "Invalid \escape"
        raw = answer("ראה \\ט ושם")
        with self.assertRaises(json.JSONDecodeError):
            json.loads(raw)
        self.assertIn("ושם", SEC._parse(raw)["notes"][0]["points"])

    def test_both_shapes_and_fences_and_prose(self):
        raw = "```json\nהנה התשובה: " + answer('לשיטת רש"י \\ק') + "\n```"
        self.assertTrue(SEC._parse(raw)["notes"][0]["points"].startswith("לשיטת רש״י"))

    def test_structural_quote_not_touched(self):      # a quote next to : , { } is JSON structure, never a gershayim
        raw = '{"notes":[{"label":"א","points":"ב"}]}'
        self.assertEqual(SEC._parse(raw), json.loads(raw))

    def test_truly_broken_still_raises_original_error(self):
        with self.assertRaises(json.JSONDecodeError) as cm:
            SEC._parse('{"notes": [ {"label": ')
        self.assertIn("Expecting", str(cm.exception))


class ReadBatch(unittest.TestCase):
    def _create(self, answers):
        it = iter(answers)

        def create(**kw):
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=next(it))])
        return create

    def test_first_answer_with_abbreviation_now_succeeds_without_reask(self):
        calls = []
        base = self._create([answer('לפי הרמב"ם')])

        def create(**kw):
            calls.append(kw)
            return base(**kw)
        self.assertEqual(SEC.read_batch(create, "m", [(LAB, "טקסט")]), {LAB: "לפי הרמב״ם"})
        self.assertEqual(len(calls), 1)

    def test_two_unrepairable_answers_still_fail_loud(self):
        with self.assertRaises(RuntimeError):
            SEC.read_batch(self._create(["not json", "still not json"]), "m", [(LAB, "טקסט")])


if __name__ == "__main__":
    unittest.main()
