"""Forbidden-name guard (Sod HaDaf). ONE algorithm, mirrored by hand in index.html (tests/test_filter_js.py keeps the two in step).

Why this exists as whole-word matching: the first version was a plain substring test, so the name "ישו" blocked any page containing
נישום, ישוב, קישות, הקישו, בישוליה, חישובו ..., and "ישוע" blocked ישועה ("salvation"). That silently stopped the lesson on at
least twelve dafs and dropped Psalms 91 and 104 from the prayer supplements.

Algorithm (identical in JS): turn the maqaf into a space, strip niqqud/cantillation and quote marks, lower-case; split into runs of Hebrew letters; a token is a hit when,
after removing up to three leading one-letter prefixes (ו ב כ ל מ ש ה), it EQUALS one of the names. The euphemism phrases
(אותו האיש / אותו איש) are matched as two consecutive tokens. Latin-letter names are matched as substrings (no Hebrew-letter false positives
are possible there).

EUPHEMISM_PHRASES_BLOCK: the phrase "אותו האיש" also occurs innocently in the Talmud (e.g. Chullin 54, "זכור אותו האיש לטוב"). With the flag True
(the owner's existing rule, kept) that daf still halts; set it to False to let phrase-only pages through while the names stay blocked.
"""
import re

_NIK = re.compile(r"[֑-ׇ]")
_QUOTES = re.compile("[\"'׳״‘’]")
_HEB_RUN = re.compile(r"[א-ת]+")

HEBREW_NAMES = ("ישו", "ישוע", "ישובע", "יזוס", "הנוצרי", "הנוצרים")
LATIN_NAMES = ("jesus", "christ", "yeshu", "jesu")
PHRASES = (("אותו", "האיש"), ("אותו", "איש"))
PREFIX_LETTERS = "ובכלמשה"
EUPHEMISM_PHRASES_BLOCK = True


def normalize(text):
    # the maqaf (U+05BE) joins two words visually: make it a space BEFORE the niqqud/cantillation range (which contains it) is stripped
    return _QUOTES.sub("", _NIK.sub("", (text or "").replace("־", " "))).lower()


def _strip_prefixes(token):
    """yield the token with 0..3 leading prefix letters removed"""
    yield token
    t = token
    for _ in range(3):
        if len(t) > 1 and t[0] in PREFIX_LETTERS:
            t = t[1:]
            yield t
        else:
            break


def forbidden_hit(text):
    """-> the matched name/phrase, or None"""
    n = normalize(text)
    for w in LATIN_NAMES:
        if w in n:
            return w
    toks = _HEB_RUN.findall(n)
    for i, tok in enumerate(toks):
        for base in _strip_prefixes(tok):
            if base in HEBREW_NAMES:
                return base
        if EUPHEMISM_PHRASES_BLOCK and i + 1 < len(toks):
            for a, b in PHRASES:
                if toks[i + 1] == b and any(x == a for x in _strip_prefixes(tok)):
                    return a + " " + b
    return None
