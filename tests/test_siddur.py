"""The siddur pages (the three weekday pages + every other part in siddur/, R2066) are the FULL Sefaria text, one section per Sefaria node, with a ref per section.

Offline (always): the published files match the snapshot taken at import (same refs, same order, same words, same hashes), nothing is a placeholder,
nothing is shortened, the text is HTML-safe (the page writes it with innerHTML).
Live (SEFARIA_LIVE=1, also the weekly 'siddur-check' workflow): the published files are re-compared with what Sefaria returns NOW, and every leaf of the
Sefaria nodes is present.  Run: python -m unittest discover -s tests -v"""
import hashlib
import html
import json
import os
import re
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
import siddur_import as SI  # noqa: E402

EXP = json.load(open(os.path.join(ROOT, "tests", "fixtures", "siddur_expected.json"), encoding="utf-8"))
PAGES = {k: json.load(open(os.path.join(ROOT, SI.path_of(k)), encoding="utf-8")) for k in SI.all_keys()}
INDEX = json.load(open(os.path.join(ROOT, "siddur", "index.json"), encoding="utf-8"))
NIK = re.compile(r"[֑-ׇ]")


class Published(unittest.TestCase):
    def test_every_section_has_a_ref_and_a_proof(self):
        for key, page in PAGES.items():
            with self.subTest(key):
                self.assertGreaterEqual(len(page["sections"]), 4 if key in SI.PAGES else 1)
                for s in page["sections"]:
                    self.assertTrue(s["ref"].startswith(SI.WORK + ", "), s["ref"])
                    self.assertTrue(s["n"].strip() and s["t"].strip(), s["ref"])
                    text = html.unescape(s["t"])
                    self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), s["sha256"], s["ref"])
                    self.assertEqual(SI.words(text), s["words"], s["ref"])
                    self.assertEqual(len([l for l in text.split("\n") if l.strip()]), s["segments"], s["ref"])
                self.assertEqual(len({s["ref"] for s in page["sections"]}), len(page["sections"]), "duplicate refs")

    def test_same_refs_in_the_same_order_as_the_import_snapshot(self):
        for key, page in PAGES.items():
            with self.subTest(key):
                self.assertEqual([(s["ref"], s["words"], s["segments"], s["sha256"]) for s in page["sections"]],
                                 [(e["ref"], e["words"], e["segments"], e["sha256"]) for e in EXP[key]["sections"]])

    def test_nothing_is_shortened_or_a_placeholder(self):
        floor = {"shacharit": 15000, "mincha": 4000, "arvit": 3300}      # the hand-written pages had ~2,400 / ~1,600 / ~1,900 words
        for key, page in PAGES.items():
            with self.subTest(key):
                self.assertGreaterEqual(sum(s["words"] for s in page["sections"]), floor.get(key, 100))
                for s in page["sections"]:
                    t = html.unescape(s["t"])
                    for bad in ("ראה בכרטיס", "ראה את הברכות המלאות", "הטקסט המלא בתהילים", "אומרים את שאר פסוקי דזמרא"):    # phrases of the old hand-written abridgments
                        self.assertFalse(bad in t, "%s contains the placeholder %r" % (s["ref"], bad))

    def test_the_text_is_html_safe_for_innerhtml(self):
        for key, page in PAGES.items():
            for s in page["sections"]:
                with self.subTest(s["ref"]):
                    self.assertNotRegex(s["t"], r"<[a-zA-Z/!]")
                    self.assertNotRegex(s["n"], r"<[a-zA-Z/!]")

    def test_the_page_keeps_the_shape_index_html_reads(self):
        for key, page in PAGES.items():
            self.assertTrue(page["title"] and page["subtitle"] and page["attribution"] and page["note"])
            self.assertTrue(all("n" in s and "t" in s for s in page["sections"]))
            self.assertEqual(page["source"]["work"], SI.WORK)
            self.assertTrue(page["source"]["license"])


class IndexAndMenu(unittest.TestCase):
    """siddur/index.json lists every published page and the page builds its menu from it (R2066)"""

    def test_index_lists_every_page_with_matching_counts(self):
        self.assertEqual([p["key"] for p in INDEX["parts"]], SI.all_keys())
        for p in INDEX["parts"]:
            with self.subTest(p["key"]):
                page = PAGES[p["key"]]
                self.assertEqual(p["file"] + ".json", SI.path_of(p["key"]).replace(os.sep, "/"))
                self.assertTrue(os.path.exists(os.path.join(ROOT, p["file"] + ".json")))
                self.assertEqual(p["sections"], len(page["sections"]))
                self.assertEqual(p["words"], sum(s["words"] for s in page["sections"]))
                self.assertIn(p["group"], INDEX["groups"])
                self.assertEqual(p["node"], page["source"]["node"])

    def test_parts_are_never_duplicated_and_have_a_known_group(self):
        nodes = [p["node"] for p in INDEX["parts"]]
        self.assertEqual(len(nodes), len(set(nodes)))
        for key, (node, group) in SI.PARTS.items():
            self.assertNotIn(node, [v[0] for v in SI.PAGES.values()])
            self.assertIn(group, SI.GROUPS)

    def test_the_page_reads_the_index_for_its_menu(self):
        h = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        self.assertIn('id="siddurMore"', h)
        self.assertIn("./siddur/index.json", h)
        for k in SI.PAGES:
            self.assertIn('data-tab="%s"' % k, h)


class Cleaning(unittest.TestCase):
    def test_flatten_and_clean(self):
        raw = [["<b>אבג</b> דהו", "זחט&nbsp;יכל"], ["שורה אחרונה<br>עוד"]]
        self.assertEqual(SI.section_text(raw), "אבג דהו\nזחט יכל\n\nשורה אחרונה\nעוד")

    def test_word_count_ignores_niqqud(self):
        self.assertEqual(SI.words("שְׁמַע יִשְׂרָאֵל"), 2)


@unittest.skipUnless(os.environ.get("SEFARIA_LIVE") == "1", "set SEFARIA_LIVE=1 to compare with Sefaria now")
class LiveAgainstSefaria(unittest.TestCase):
    def test_every_page_is_identical_to_sefaria_now(self):
        cwd = os.getcwd()
        os.chdir(ROOT)
        try:
            for key in PAGES:
                with self.subTest(key):
                    self.assertEqual(SI.compare(key, SI.fetch_page(key)), [])
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
