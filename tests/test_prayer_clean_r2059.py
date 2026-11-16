"""R2059 F3: the prayer-card cleaner keeps words whole and drops footnotes, parasha markers and entities. Offline (raw strings are
Sefaria's own markup, captured 2026-11-16). Goes into the repo as tests/test_prayer_clean_r2059.py."""
import os, re, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine as E

SHEMA = 'שְׁמַ֖<big>ע</big> יִשְׂרָאֵ֑ל יְהֹוָ֥ה אֱלֹהֵ֖ינוּ יְהֹוָ֥ה&thinsp;<small>׀</small>&thinsp;אֶחָֽ<big>ד</big>׃'
DEUT_11_21 = ('לְמַ֨עַן יִרְבּ֤וּ יְמֵיכֶם֙ כִּימֵ֥י הַשָּׁמַ֖יִם עַל־הָאָֽרֶץ<sup class="footnote-marker">*</sup><i class="footnote">(בספרי תימן הָאָֽרֶ<big>ץ</big> '
              'בצד״י גדולה)</i>׃&nbsp;<span class="mam-spi-samekh">{ס}</span>&nbsp;&nbsp;&nbsp;')
EX_34_7 = '<big>נֹ</big>צֵ֥ר<sup class="footnote-marker">*</sup><i class="footnote">(בספרי תימן נֹצֵ֥ר בנו״ן רגילה)</i> חֶ֙סֶד֙ לָאֲלָפִ֔ים פֹּקֵ֣ד&thinsp;<b>׀</b> עֲוֺ֣ן'
_NIK = re.compile(r"[֑-ֽֿׁׂׄ-ׇ͏]")      # vowels + cantillation only; keeps ׀ (U+05C0) and ׃ (U+05C3)


def plain(s):
    return _NIK.sub("", s)


class Clean(unittest.TestCase):
    def test_shema_words_whole(self):
        t = " ".join(E._clean_prayer([SHEMA]))
        self.assertIn("שמע ישראל", plain(t))
        self.assertTrue(plain(t).endswith("אחד׃"))
        self.assertNotIn("&", t)

    def test_old_clean_split_them(self):      # documents the defect this fixes
        self.assertIn("שמ ע", plain(" ".join(E._clean([SHEMA]))))

    def test_footnote_and_parasha_marker_removed(self):
        t = " ".join(E._clean_prayer([DEUT_11_21]))
        self.assertNotIn("בספרי תימן", t)
        self.assertNotIn("{ס}", t)
        self.assertNotIn("*", t)
        self.assertTrue(plain(t).endswith("הארץ׃"))

    def test_large_first_letter_kept_on_word(self):
        t = plain(" ".join(E._clean_prayer([EX_34_7])))
        self.assertTrue(t.startswith("נצר חסד"))
        self.assertIn("פקד ׀ עון", t)

    def test_nested_lists_and_empty(self):
        self.assertEqual(E._clean_prayer([["", None], []]), [])


if __name__ == "__main__":
    unittest.main()
