"""Read the WHOLE daf and the WHOLE of every commentary, in sections (R1116).

Before: write_lesson() sent daf['hebrew'][:3200] (about half of the daf), mishnayot[:900], each commentary [:400] to the model.
Now: every source is split into sections on segment/sentence boundaries (never mid-word, never dropped), the sections are packed into
batches and each batch is read by the model on its own (map step -> short notes per section). The lesson writer then receives ALL the notes.
No source text is cut; a batch that comes back without a note for every section it was given is retried once and then fails the run (loudly).

Pure functions + an injected `create` callable, so the tests run offline with a fake model.
"""
import json
import re

SECTION_CHARS = 2800     # one section of one source (split on sentence/segment boundaries)
BATCH_CHARS = 6500       # sections packed into one model call


def split_sections(text, max_chars=SECTION_CHARS):
    """Split a text into pieces of at most max_chars, only at sentence/clause boundaries, else at spaces (never inside a word). Lossless: ' '.join(parts) == text (normalised whitespace)."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    out = []
    while len(text) > max_chars:
        window = text[:max_chars]
        cut = max(window.rfind(". "), window.rfind("? "), window.rfind("! "), window.rfind(": "), window.rfind("׃ "))
        if cut < max_chars // 2:
            cut = max(cut, window.rfind("; "), window.rfind(", "))
        if cut < max_chars // 3:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = max_chars          # a single unbroken run longer than the limit: hard cut (cannot happen in prose)
        else:
            cut += 1
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        out.append(text)
    return out


def make_units(daf, mishnayot, comms):
    """-> [(label, text)] with EVERY character of the daf, the mishnayot and each commentary present, split into sections"""
    units = []
    seen = {}

    def add(name, text):
        n = seen.get(name, 0) + 1
        seen[name] = n
        if n > 1:                      # two sources with the same name (e.g. two commentators called alike): labels must stay unique
            name = "%s (#%d)" % (name, n)
        parts = split_sections(text)
        for i, p in enumerate(parts, 1):
            units.append((name if len(parts) == 1 else "%s — חלק %d/%d" % (name, i, len(parts)), p))

    add("הדף " + (daf.get("he_ref") or daf.get("ref") or ""), daf.get("hebrew", ""))
    for m in mishnayot:
        add("משנה " + (m.get("he_ref") or ""), m.get("hebrew", ""))
    for c in comms:
        add("מפרש: " + c.get("name", "?"), c.get("text", ""))
    return units


def pack_batches(units, max_chars=BATCH_CHARS):
    batches, cur, size = [], [], 0
    for u in units:
        if cur and size + len(u[1]) > max_chars:
            batches.append(cur)
            cur, size = [], 0
        cur.append(u)
        size += len(u[1])
    if cur:
        batches.append(cur)
    return batches


MAP_SYSTEM = ("אתה לומד תורני. אתה קורא קטעים מתוך מקורות של הדף היומי (גמרא, משנה, מפרשים), בעברית בלבד. הטקסט עשוי להיות בארמית — הבן ותרגם לעברית. "
              "לכל קטע כתוב הערה נאמנה לטקסט: מה נאמר, המונחים והמחלוקות המרכזיים, ביטויים בארמית מתורגמים. אל תמציא ציטוטים ואל תוסיף דבר שאינו בקטע.")


# R2059 F1: the two failure shapes measured in the Daily Agent logs 2026-11-08..15 (every failed run, step read_in_sections):
#   "Expecting ',' delimiter"  -- a Hebrew abbreviation copied from the source puts a bare " inside a JSON string (the R1601 class,
#                                 already repaired in engine._answer_json, but this module had its own parser without the repair);
#   "Invalid \\escape"          -- a backslash the model copied/typed that is not a JSON escape.
# Both repairs run ONLY after a failed parse, in this order, and a valid answer is never touched.
_HEB_ABBREV_QUOTE = re.compile(r'(?<=[\u05d0-\u05ea])"(?=[\u05d0-\u05ea])')
_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def _candidates(raw):
    yield raw
    q = _HEB_ABBREV_QUOTE.sub("\u05f4", raw)
    if q != raw:
        yield q
    e = _BAD_ESCAPE.sub(r"\\\\", q)
    if e != q:
        yield e


def _parse(raw):
    raw = (raw or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    first = None
    for cand in _candidates(raw):
        for text in (cand, cand[cand.find("{"):cand.rfind("}") + 1] if cand.find("{") != -1 and cand.rfind("}") > cand.find("{") else None):
            if text is None:
                continue
            try:
                return json.loads(text)
            except Exception as ex:      # noqa: BLE001
                first = first or ex
    raise first


def read_batch(create, model, batch):
    """one model call over one batch -> {label: note}; raises if any section came back without a note"""
    body = "\n\n".join("[[%s]]\n%s" % (lab, txt) for lab, txt in batch)
    user = ("להלן קטעים, כל אחד תחת כותרתו בסוגריים מרובעים כפולים. עבור כל קטע החזר הערה.\n\n" + body +
            "\n\nהחזר JSON תקין בלבד: {\"notes\":[{\"label\":\"הכותרת בדיוק כפי שניתנה\",\"points\":\"2-5 משפטים: מה בקטע, המונחים והמחלוקות, תרגום ארמית\"}]} — פריט אחד לכל קטע, בלי להחסיר אף קטע.")
    last, ask = None, user
    for attempt in (1, 2):
        msg = create(model=model, max_tokens=2500, temperature=0.2, system=MAP_SYSTEM, messages=[{"role": "user", "content": ask}])
        raw = "".join(b.text for b in msg.content if b.type == "text")
        try:
            notes = _parse(raw).get("notes", [])
            got = {n.get("label"): (n.get("points") or "").strip() for n in notes if isinstance(n, dict)}
            missing = [lab for lab, _ in batch if not got.get(lab)]
            if not missing:
                return {lab: got[lab] for lab, _ in batch}
            last = "missing notes for: " + "; ".join(missing)[:200]
        except Exception as ex:           # noqa: BLE001
            last = "unparseable answer: %s" % ex
            print("UNPARSEABLE_SECTION_JSON attempt %d (first 800 chars): %s" % (attempt, raw[:800].replace("\n", "\\n")))      # R2059: keep the evidence
            ask = user + "\n\nReturn ONLY valid JSON"     # R1533: the second attempt asks for JSON only
    raise RuntimeError("section reading failed after 2 attempts (%s)" % last)


def read_in_sections(create, units, model, max_batch=BATCH_CHARS):
    """-> [{'label','points'}] in the original order, one per unit; raises on any gap"""
    notes = {}
    for batch in pack_batches(units, max_batch):
        notes.update(read_batch(create, model, batch))
    return [{"label": lab, "points": notes[lab]} for lab, _ in units]


def notes_digest(notes):
    return "\n".join("• [%s] %s" % (n["label"], n["points"]) for n in notes)
