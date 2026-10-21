"""Whole-word forbidden-name guard. Run: python -m unittest discover -s tests -v   (from the repo root)"""
import json
import os
import re
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
import forbidden as F  # noqa: E402

FIX = json.load(open(os.path.join(ROOT, "tests", "fixtures", "blocked_dafs.json"), encoding="utf-8"))["dafs"]

# the filter that was live until now (plain substring), kept ONLY to prove the regression
_NIK = re.compile(r"[֑-ׇ]")
_OLD = {"ישו", "ישוע", "יזוס", "הנוצרי", "אותו האיש", "jesus", "christ", "yeshu", "jesu"}


def old_hit(text):
    norm = lambda s: re.sub("[\"'׳״‘’]", "", _NIK.sub("", s or "")).lower()
    n = norm(text)
    return next((t for t in _OLD if norm(t) in n), None)


class TwelveBlockedDafs(unittest.TestCase):
    def test_fixture_is_the_twelve(self):
        self.assertEqual(len(FIX), 12)

    def test_old_substring_filter_blocked_all_twelve(self):
        for ref, txt in FIX.items():
            with self.subTest(ref):
                self.assertIsNotNone(old_hit(txt), ref)

    def test_whole_word_filter_passes_eleven_and_names_the_twelfth(self):
        for ref, txt in FIX.items():
            with self.subTest(ref):
                hit = F.forbidden_hit(txt)
                if ref == "Chullin 54":     # "זכור אותו האיש לטוב": blocked only by the euphemism PHRASE rule (owner's existing rule, kept)
                    self.assertEqual(hit, "אותו האיש")
                else:
                    self.assertIsNone(hit, (ref, hit))

    def test_with_the_phrase_rule_off_all_twelve_pass(self):
        old = F.EUPHEMISM_PHRASES_BLOCK
        F.EUPHEMISM_PHRASES_BLOCK = False
        try:
            for ref, txt in FIX.items():
                with self.subTest(ref):
                    self.assertIsNone(F.forbidden_hit(txt), ref)
        finally:
            F.EUPHEMISM_PHRASES_BLOCK = old


class Words(unittest.TestCase):
    MUST_BLOCK = ["ישו", "וישו", "לישו", "בישו", "הישו", "ישוע", "ישוע הנוצרי", "הנוצרי", "הנוצרים", "יזוס", "אותו האיש", "ואותו איש",
                  "Jesus", "JESUS", "christ", "Christian", "yeshu", "Yeshua", "בְּיֵשׁוּ".replace("ְ", "").replace("ֵ", "").replace("ּ", ""),
                  "אמר ישו לתלמידיו", "ישו.", "(ישו)", "ישו־ישו"]
    MUST_PASS = ["נישום", "ישוב", "ישובון", "קישות", "הקישו", "בישוליה", "חישובו", "ישועה", "בישועתי", "ישועת", "ישוד", "באשישות", "ישועות",
                 "דבר שאינו נישום מחיים", "ואראהו בישועתי", "אל עפרם ישובון", "מקטב ישוד צהרים", "אותו הדבר", "אותו הזמן"]

    def test_names_still_blocked(self):
        for t in self.MUST_BLOCK:
            with self.subTest(t):
                self.assertIsNotNone(F.forbidden_hit(t), t)

    def test_ordinary_words_pass(self):
        for t in self.MUST_PASS:
            with self.subTest(t):
                self.assertIsNone(F.forbidden_hit(t), t)

    def test_niqqud_and_quotes_do_not_hide_a_name(self):
        self.assertIsNotNone(F.forbidden_hit("יֵשׁוּ".replace("ֵ", "").replace("ׁ", "").replace("ּ", "")))
        self.assertIsNotNone(F.forbidden_hit('י"שו'))


if __name__ == "__main__":
    unittest.main()
