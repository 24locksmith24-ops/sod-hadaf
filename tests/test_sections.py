"""The whole daf and every commentary reach the model, in sections (R1116). Offline: a fake model that records every prompt."""
import json
import os
import re
import sys
import types
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _anthropic_stub  # noqa: E402
_anthropic_stub.install()                  # offline stand-in only when the real SDK is not installed (R1526)
import sections as S  # noqa: E402
import engine as E  # noqa: E402

DAFS = json.load(open(os.path.join(ROOT, "tests", "fixtures", "blocked_dafs.json"), encoding="utf-8"))["dafs"]
BEK32 = DAFS["Bekhorot 32"]          # 5,605 characters: the old code sent only the first 3,200
CHUL124 = DAFS["Chullin 124"]        # 7,182 characters
norm = lambda t: re.sub(r"\s+", " ", t).strip()


class FakeModel:
    """answers every map call with a note for each [[label]] it was shown; records the prompts"""
    def __init__(self, drop_first_n_attempts=0):
        self.prompts = []
        self.calls = 0
        self.drop = drop_first_n_attempts

    def __call__(self, **kw):
        self.calls += 1
        user = kw["messages"][0]["content"]
        self.prompts.append(user)
        labels = re.findall(r"\[\[(.+?)\]\]", user)
        if self.calls <= self.drop and labels:
            labels = labels[1:]                      # forgets one section
        out = json.dumps({"notes": [{"label": l, "points": "הערה על " + l} for l in labels]}, ensure_ascii=False)
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=out)])


class Splitting(unittest.TestCase):
    def test_lossless_bounded_and_never_inside_a_word(self):
        for txt in (BEK32, CHUL124, "קצר", ""):
            with self.subTest(len(txt)):
                parts = S.split_sections(txt)
                self.assertEqual(" ".join(parts), norm(txt))
                self.assertTrue(all(len(p) <= S.SECTION_CHARS for p in parts))
        self.assertGreaterEqual(len(S.split_sections(BEK32)), 2)
        self.assertGreaterEqual(len(S.split_sections(CHUL124)), 3)

    def test_a_long_text_without_punctuation_splits_at_spaces(self):
        t = " ".join(["מילה"] * 2000)
        parts = S.split_sections(t, 500)
        self.assertEqual(" ".join(parts), t)
        self.assertTrue(all(len(p) <= 500 and p.split(" ")[0] == "מילה" for p in parts))


class Units(unittest.TestCase):
    def sources(self):
        daf = {"he_ref": "בכורות לב", "ref": "Bekhorot 32", "hebrew": BEK32}
        mish = [{"he_ref": "משנה א", "hebrew": "שורה " * 50}, {"he_ref": "משנה ב", "hebrew": "שורה " * 60}]
        comms = [{"name": "רש\"י", "text": CHUL124}, {"name": "רש\"י", "text": "פירוש שני בשם זהה"}, {"name": "תוספות", "text": "קצר"}]
        return daf, mish, comms

    def test_every_character_of_every_source_is_present(self):
        daf, mish, comms = self.sources()
        units = S.make_units(daf, mish, comms)
        got = norm(" ".join(t for _, t in units))
        want = norm(" ".join([daf["hebrew"]] + [m["hebrew"] for m in mish] + [c["text"] for c in comms]))
        self.assertEqual(got, want)
        self.assertEqual(len({l for l, _ in units}), len(units), "labels must be unique, even for two commentators with the same name")

    def test_batches_keep_order_and_size(self):
        daf, mish, comms = self.sources()
        units = S.make_units(daf, mish, comms)
        batches = S.pack_batches(units)
        self.assertEqual([u for b in batches for u in b], units)
        self.assertTrue(all(sum(len(t) for _, t in b) <= S.BATCH_CHARS or len(b) == 1 for b in batches))


class Reading(unittest.TestCase):
    def test_the_end_of_the_daf_reaches_the_model(self):
        daf = {"he_ref": "בכורות לב", "ref": "Bekhorot 32", "hebrew": BEK32}
        units = S.make_units(daf, [], [])
        fake = FakeModel()
        notes = S.read_in_sections(fake, units, "m")
        sent = " ".join(fake.prompts)
        self.assertIn(norm(BEK32)[-120:], sent)               # the old [:3200] cut would have lost this
        self.assertIn(norm(BEK32)[3300:3400], sent)
        self.assertEqual([n["label"] for n in notes], [l for l, _ in units])

    def test_a_forgotten_section_is_retried_once(self):
        units = S.make_units({"he_ref": "x", "hebrew": CHUL124}, [], [])
        fake = FakeModel(drop_first_n_attempts=1)
        notes = S.read_in_sections(fake, units, "m")
        self.assertEqual(len(notes), len(units))
        self.assertGreater(fake.calls, len(S.pack_batches(units)))

    def test_a_section_that_stays_missing_fails_loudly(self):
        units = S.make_units({"he_ref": "x", "hebrew": CHUL124}, [], [])
        with self.assertRaises(RuntimeError):
            S.read_in_sections(FakeModel(drop_first_n_attempts=99), units, "m")


class LessonPrompt(unittest.TestCase):
    """write_lesson / connector_agent get the notes of ALL sections, not cut text"""
    def setUp(self):
        self.seen = []
        outer = self

        class Client:
            class messages:
                @staticmethod
                def create(**kw):
                    outer.seen.append(kw)
                    return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text='{"title":"t","theme":"x","links":[]}')])
        self.real = E.anthropic
        E.anthropic = types.SimpleNamespace(Anthropic=lambda *a, **k: Client, __version__="0.125.0")

    def tearDown(self):
        E.anthropic = self.real
        E.FAILURES.clear()

    def test_write_lesson_prompt_carries_all_section_notes(self):
        daf = {"he_ref": "בכורות לב", "ref": "Bekhorot 32", "hebrew": BEK32}
        comms = [{"name": "רש\"י", "text": CHUL124, "from": "x"}]
        units = S.make_units(daf, [], comms)
        notes = S.read_in_sections(FakeModel(), units, "m")
        E.write_lesson(daf, [], comms, None, [], [], {"els": [], "atbash": [], "notemarikon": {}, "letters_scanned": 0}, {}, [], notes)
        user = self.seen[-1]["messages"][0]["content"]
        for n in notes:
            self.assertIn(n["label"], user)
        self.assertNotIn(BEK32[:200], user)                   # the raw cut text is no longer pasted in; the notes of every section are

    def test_connector_prompt_carries_all_section_notes(self):
        daf = {"he_ref": "בכורות לב", "ref": "Bekhorot 32", "hebrew": BEK32}
        notes = S.read_in_sections(FakeModel(), S.make_units(daf, [], []), "m")
        E.connector_agent(daf, [], [], None, [], [], [], notes)
        user = self.seen[-1]["messages"][0]["content"]
        for n in notes:
            self.assertIn(n["label"], user)


class PageSlice(unittest.TestCase):
    def test_the_page_no_longer_slices_the_daf(self):
        html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        self.assertNotIn("dafTextGlobal.slice", html)
        self.assertIn("const snippet = dafTextGlobal;", html)


if __name__ == "__main__":
    unittest.main()
