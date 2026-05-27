#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
engine.py — סוֹד הַדַּף, מנוע בקובץ אחד.

קובץ עצמאי אחד שעושה הכול:
  • שומר מנוחה: לא פועל בשישי/שבת/חג.
  • מושך את הדף יומי + שתי המשניות היומיות מ-Sefaria.
  • מסנן מקורות: עברית בלבד, קטגוריות מאושרות, וחוסם כל עמוד שמזכיר שם חסום.
  • כותב שיעור יומי עשיר (Claude) ושומר אותו ל-discoveries.json.

הרצה (ב-GitHub Actions או מקומית):
    export ANTHROPIC_API_KEY=sk-ant-...
    python engine.py
"""

import os, re, json, datetime, sys
import requests
import anthropic

MODEL = os.environ.get("TORAH_MODEL", "claude-sonnet-4-6")
SEF = "https://www.sefaria.org/api"
HEBCAL = "https://www.hebcal.com/hebcal"
OUT = "discoveries.json"

# ---------- גימטריה (חישוב ודאי) ----------
GEM = {"א":1,"ב":2,"ג":3,"ד":4,"ה":5,"ו":6,"ז":7,"ח":8,"ט":9,"י":10,"כ":20,"ך":20,
       "ל":30,"מ":40,"ם":40,"נ":50,"ן":50,"ס":60,"ע":70,"פ":80,"ף":80,"צ":90,"ץ":90,
       "ק":100,"ר":200,"ש":300,"ת":400}
_NIK = re.compile(r"[\u0591-\u05C7]")
def gem(w): return sum(GEM.get(c,0) for c in re.sub(r"[^\u05D0-\u05EA]","",w))
def equal_pairs(text):
    from collections import defaultdict
    b = defaultdict(set)
    for w in _NIK.sub("", text).split():
        n = re.sub(r"[^\u05D0-\u05EA]","",w)
        if len(n) >= 3: b[gem(n)].add(n)
    return {v:sorted(s) for v,s in b.items() if len(s) >= 2 and v >= 30}

# ---------- צפנים בתורה (חישוב ודאי, דטרמיניסטי) ----------
ALEFBET = "אבגדהוזחטיכלמנסעפצקרשת"
_FINALS = {"ך":"כ","ם":"מ","ן":"נ","ף":"פ","ץ":"צ"}
ATBASH = {c: ALEFBET[21-i] for i, c in enumerate(ALEFBET)}
CODE_TARGETS = ["תורה","ישראל","שלום","אמת","משה","אהבה","חיים","אדם"]

def to_consonants(text):
    """הופך טקסט עברי לרצף עיצורים בלבד (בלי ניקוד/רווחים), אותיות סופיות -> רגילות."""
    s = _NIK.sub("", text or "")
    s = "".join(_FINALS.get(c, c) for c in s if "\u05D0" <= c <= "\u05EA")
    return s

def atbash_word(w):
    base = to_consonants(w)
    return "".join(ATBASH.get(c, c) for c in base)

def notarikon(text, n_words=7):
    words = [re.sub(r"[^\u05D0-\u05EA]","", _NIK.sub("", w)) for w in (text or "").split()]
    words = [w for w in words if w]
    initials = "".join(w[0] for w in words[:n_words])
    finals = "".join(w[-1] for w in words[:n_words])
    return {"initials": initials, "finals": finals}

def els_search(letters, target, max_skip=80, max_hits=3):
    """דילוגי אותיות יעיל: מאתר את 'target' במרווחים שווים (מדילוג 2 ומעלה,
    כדי לדלג על הופעה רגילה בטקסט), קדימה ואחורה. מחזיר מיקומים אמיתיים."""
    t = to_consonants(target); tr = t[::-1]
    n, m = len(letters), len(t)
    hits = []
    if m < 2 or n < m: return hits
    for skip in range(2, max_skip+1):
        for r in range(skip):
            sub = letters[r::skip]
            i = sub.find(t)
            while i != -1:
                hits.append({"word": target, "skip": skip, "dir": "→", "start": r + i*skip})
                if len(hits) >= max_hits: return hits
                i = sub.find(t, i+1)
            i = sub.find(tr)
            while i != -1:
                hits.append({"word": target, "skip": skip, "dir": "←", "start": r + (i+m-1)*skip})
                if len(hits) >= max_hits: return hits
                i = sub.find(tr, i+1)
    return hits

def build_codes(text):
    letters = to_consonants(text)
    els = []
    for tgt in CODE_TARGETS:
        for h in els_search(letters, tgt, max_skip=80, max_hits=2):
            els.append(h)
        if len(els) >= 8: break
    not_ = notarikon(text)
    atb = [{"word": w, "atbash": atbash_word(w)} for w in ["תורה","ישראל","שבת"]]
    return {"els": els, "notarikon": not_, "atbash": atb, "letters_scanned": len(letters)}

def hit_context(letters, h, max_strip=160):
    """מחזיר את רצף האותיות הרציף שחוצה את הדילוג, עם מיקומי אותיות-המילה לסימון."""
    skip = h["skip"]; m = len(to_consonants(h["word"])); start = h["start"]
    if (m-1)*skip + 1 > max_strip:
        return None
    if h["dir"] == "→":
        lo, hi = start, start + (m-1)*skip
    else:
        hi, lo = start, start - (m-1)*skip
    if lo < 0 or hi >= len(letters): return None
    strip = letters[lo:hi+1]
    marks = sorted(set(k*skip for k in range(m)))
    return {"strip": strip, "marks": marks}

def enrich(letters, hits):
    for h in hits:
        ctx = hit_context(letters, h)
        if ctx: h.update(ctx)
    return hits

def parasha_targets(name):
    """מילים עבריות מתוך שם הפרשה, כמטרות חיפוש דילוגים לאותו שבוע."""
    clean = _NIK.sub("", name or "")
    return [w for w in re.findall(r"[\u05D0-\u05EA]{3,}", clean) if w not in ("פרשת",)]

def build_codes_record(today, torah_text, parasha_text, parasha_name):
    """רשומת צפנים נפרדת: חיפוש בכל חמשת החומשים + בפרשת השבוע."""
    tl = to_consonants(torah_text)
    full = {"scope": "חמישה חומשים", "letters": len(tl), "els": []}
    for tgt in CODE_TARGETS:
        for h in els_search(tl, tgt, max_skip=80, max_hits=2):
            full["els"].append(h)
        if len(full["els"]) >= 10: break
    enrich(tl, full["els"])
    # צפנים "לאותו שבוע" — שם הפרשה כדילוג בתוך כל התורה
    week = {"name": parasha_name, "els": []}
    for tgt in parasha_targets(parasha_name):
        for h in els_search(tl, tgt, max_skip=150, max_hits=2):
            week["els"].append(h)
    enrich(tl, week["els"])
    # צפנים בתוך טקסט הפרשה עצמה
    pl = to_consonants(parasha_text or "")
    par = {"letters": len(pl), "els": [], "gematria": [], "notarikon": notarikon(parasha_text or "")}
    for tgt in CODE_TARGETS:
        for h in els_search(pl, tgt, max_skip=60, max_hits=2):
            par["els"].append(h)
        if len(par["els"]) >= 8: break
    enrich(pl, par["els"])
    for v, words in list(equal_pairs(parasha_text or "").items())[:6]:
        par["gematria"].append({"value": v, "words": words})
    return {
        "date": today.isoformat(),
        "parasha": parasha_name,
        "full_torah": full,
        "week_codes": week,
        "parasha_codes": par,
        "note": ("הצפנים מחושבים בקוד מן הטקסט המלא של חמישה חומשי תורה ומפרשת השבוע. "
                 "דפוסי דילוג מופיעים גם במקרה בכל טקסט ארוך — ללימוד והתבוננות, לא הוכחה."),
    }

CODES_OUT = "codes.json"
def save_codes(record):
    data = []
    if os.path.exists(CODES_OUT):
        try: data = json.load(open(CODES_OUT, encoding="utf-8"))
        except Exception: data = []
    # ארכיון מצטבר: שומרים הכול, מאפשרים כמה ריצות ביום (לפי חותמת זמן)
    ts = record.get("ts")
    data = [r for r in data if r.get("ts") != ts]
    data.insert(0, record)
    data = data[:400]                       # שמירת ארכיון ארוך, בגבול סביר לגודל הקובץ
    json.dump(data, open(CODES_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# ---------- יומן הסוכנים (log.json): מה כל סוכן עשה בכל ריצה ----------
LOG_OUT = "log.json"
def append_log(entry):
    data = []
    if os.path.exists(LOG_OUT):
        try: data = json.load(open(LOG_OUT, encoding="utf-8"))
        except Exception: data = []
    data.insert(0, entry)
    json.dump(data[:200], open(LOG_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# ---------- שומר מקורות ----------
APPROVED = {"Tanakh","Targum","Mishnah","Talmud","Tosefta","Midrash","Halakhah",
            "Kabbalah","Liturgy","Jewish Thought","Chasidut","Musar","Responsa",
            "Commentary","Quoting Commentary","Reference"}
FORBIDDEN = {"ישו","ישוע","יזוס","הנוצרי","אותו האיש","jesus","christ","yeshu","jesu"}
def _norm(s): return re.sub(r"[\"'\u05F3\u05F4\u2018\u2019]","",_NIK.sub("",s or "")).lower()
def forbidden_hit(text):
    n = _norm(text)
    return next((t for t in FORBIDDEN if _norm(t) in n), None)
def has_hebrew(s): return bool(re.search(r"[\u05D0-\u05EA]", s or ""))

# ---------- שומר מנוחה (שבת/חג) ----------
def resting_reason(today):
    wd = today.weekday()                 # Fri=4, Sat=5
    if wd == 4: return "יום שישי — ערב שבת"
    if wd == 5: return "שבת קודש"
    try:
        r = requests.get(HEBCAL, timeout=20, params={"v":1,"cfg":"json","maj":"on",
            "min":"off","mod":"off","geo":"none","year":today.year,"month":today.month})
        for it in r.json().get("items", []):
            if it.get("date") == today.isoformat() and it.get("category") == "holiday":
                return "חג: " + (it.get("hebrew") or it.get("title"))
    except Exception:
        pass
    return None

# ---------- Sefaria ----------
def _clean(x):
    out = []
    def walk(v):
        if isinstance(v, list):
            for e in v: walk(e)
        elif v: out.append(re.sub(r"\s+"," ", re.sub(r"<[^>]+>"," ", v)).strip())
    walk(x); return [s for s in out if s]

def daily_items():
    return {(i.get("title") or {}).get("en"): i for i in
            requests.get(f"{SEF}/calendars", timeout=25).json().get("calendar_items", [])}

TORAH_BOOKS = ["Genesis","Exodus","Leviticus","Numbers","Deuteronomy"]
def fetch_full_torah():
    """מושך את הטקסט המלא של חמישה חומשי תורה (נחלת הכלל) ל-ELS."""
    parts = []
    for b in TORAH_BOOKS:
        try:
            d = requests.get(f"{SEF}/texts/{b}", timeout=90,
                             params={"context":0,"pad":0}).json()
            parts.append(" ".join(_clean(d.get("he", []))))
        except Exception:
            pass
    return " ".join(parts)

def get_text(ref):
    d = requests.get(f"{SEF}/texts/{requests.utils.quote(ref)}", timeout=25,
                     params={"context":0,"pad":0}).json()
    segs = _clean(d.get("he", []))
    return {"ref": d.get("ref", ref), "he_ref": d.get("heRef", ref),
            "hebrew": " ".join(segs), "segments": segs}

def get_commentaries(ref, cap=10):
    links = requests.get(f"{SEF}/links/{requests.utils.quote(ref)}", timeout=25,
                         params={"with_text":1}).json()
    out, seen = [], set()
    for ln in links:
        cat = ln.get("category")
        name = (ln.get("collectiveTitle") or {}).get("he") or ln.get("index_title","?")
        txt = " ".join(_clean(ln.get("he", [])))
        if not txt or not has_hebrew(txt): continue
        if forbidden_hit(txt) or forbidden_hit(name): continue
        if cat not in APPROVED: continue
        if name in seen: continue
        seen.add(name)
        out.append({"name":name, "text":txt, "cat":cat,
                    "ref":(ln.get("sourceRef") or ln.get("ref") or ref), "from":ref})
        if len(out) >= cap: break
    return out

# ---------- ספריית המקורות (סוכן ספרן): צובר מקורות וממאיפה הובאו ----------
LIB_OUT = "library.json"
def update_library(items, today_iso):
    lib = []
    if os.path.exists(LIB_OUT):
        try: lib = json.load(open(LIB_OUT, encoding="utf-8"))
        except Exception: lib = []
    by_name = {e["name"]: e for e in lib}
    for it in items:
        nm = it.get("name"); 
        if not nm: continue
        e = by_name.get(nm)
        if e:
            e["count"] = e.get("count",1) + 1
            e["last_seen"] = today_iso
            froms = set(e.get("from", [])); froms.add(it.get("from",""))
            e["from"] = [f for f in froms if f][:8]
        else:
            e = {"name":nm, "category":it.get("cat",""), "ref":it.get("ref",""),
                 "first_seen":today_iso, "last_seen":today_iso, "count":1,
                 "from":[it.get("from","")] if it.get("from") else []}
            by_name[nm] = e; lib.append(e)
    lib.sort(key=lambda e:(-e.get("count",1), e["name"]))
    json.dump(lib[:500], open(LIB_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return lib

# ---------- זיכרון משותף (insights.json): הסוכנים לומדים ומשתפים ----------
INS_OUT = "insights.json"
def load_insights(n=8):
    if os.path.exists(INS_OUT):
        try: return json.load(open(INS_OUT, encoding="utf-8"))[:n]
        except Exception: return []
    return []
def save_insight(record):
    data = load_insights(400)
    data = [r for r in data if r.get("ts") != record.get("ts")]
    data.insert(0, record)
    json.dump(data[:400], open(INS_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# ---------- מילים משותפות בין כל המקורות (חיבורים אמיתיים, בקוד) ----------
_STOP = {"אשר","אשר","את","של","על","כל","הוא","היא","אם","כי","לא","אל","עם","הזה",
         "אלה","אלו","ואת","וכל","וגם","אבל","או","גם","יש","אין","הם","הן","זה","זאת"}
def shared_words(sources, minlen=4, top=12):
    from collections import defaultdict
    where = defaultdict(set)
    for label, text in sources:
        for w in set(re.findall(r"[\u05D0-\u05EA]{%d,}"%minlen, _NIK.sub("", text or ""))):
            if w not in _STOP: where[w].add(label)
    shared = [(w, sorted(ls)) for w, ls in where.items() if len(ls) >= 2]
    shared.sort(key=lambda x:(-len(x[1]), -len(x[0])))
    return shared[:top]

# ---------- סוכן בודק התפילות: אוסף מקורות נחלת-הכלל להשלמת החסר ----------
# כל מקור הוא טקסט מדויק נחלת הכלל (תורה/תהילים) — לא ממציאים נוסח תפילה
PRAYER_SOURCES = {
    "קריאת שמע (שלוש פרשיות)": ["Deuteronomy 6:4-9", "Deuteronomy 11:13-21", "Numbers 15:37-41"],
    "אשרי — תהילה לדוד": ["Psalms 145"],
    "יושב בסתר (למנצח)": ["Psalms 91"],
    "מזמור שיר ליום השבת": ["Psalms 92"],
    "ה' מלך (לכבוד שבת)": ["Psalms 93"],
    "הללויה הללו אל בקדשו": ["Psalms 150"],
    "ברכי נפשי": ["Psalms 104"],
    "שיר המעלות (לפני ברכת המזון)": ["Psalms 126"],
    "פרשת העקדה": ["Genesis 22:1-19"],
    "עשרת הדברות": ["Exodus 20:1-14"],
    "פרשת הקרבנות (פתיחה)": ["Numbers 28:1-8"],
    "וידוי / י\"ג מידות": ["Exodus 34:6-7"],
}
PRAYERS_OUT = "prayers_audit.json"
def prayers_audit():
    out = {}
    for name, refs in PRAYER_SOURCES.items():
        parts = []
        for rf in refs:
            try:
                t = get_text(rf)
                if t["hebrew"] and not forbidden_hit(t["hebrew"]):
                    parts.append({"ref": rf, "he_ref": t["he_ref"], "text": t["hebrew"]})
            except Exception:
                pass
        if parts:
            out[name] = parts
    rec = {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "prayers": out,
           "note": ("מקורות מדויקים מנחלת הכלל (תורה/תהילים) להשלמת החלקים החסרים. "
                    "נוסח התפילה משתנה בין עדות — יש לבדוק מול סידור הקהילה.")}
    json.dump(rec, open(PRAYERS_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return rec

# ---------- סוכן מקשר (Claude): מוצא קשרים בין כל המקורות ----------
def connector_agent(daf, mishnayot, comms, parasha, extras, shared, prior):
    client = anthropic.Anthropic()
    pool = [f"דף: {daf['hebrew'][:900]}"]
    for m in mishnayot: pool.append(f"משנה: {m['hebrew'][:500]}")
    if parasha: pool.append(f"פרשה: {parasha['hebrew'][:800]}")
    for e in extras: pool.append(f"{e['he_ref']}: {e['hebrew'][:500]}")
    for c in comms[:8]: pool.append(f"{c['name']}: {c['text'][:300]}")
    sw = "; ".join(f"{w} ({'/'.join(ls)})" for w, ls in shared) or "אין"
    pr = "; ".join(p.get("theme","") for p in prior if p.get("theme")) or "אין"
    prl = "; ".join(l for p in prior for l in p.get("links_pts", [])[:2]) or "אין"
    sysmsg = (
        "אתה לומד תורני שמקשר בין מקורות בדרך הלימוד של הגמרא. בעברית בלבד.\n"
        "הטקסט עשוי להיות בארמית — הָבֵן אותו ותרגם לעברית לפני שתקשר.\n"
        "למד לקשר כדרך הגמרא, שמוציאה מכל פסוק, מילה ואות את הקשרהּ, באמצעות "
        "מידות הדרש וההיגיון התלמודי: גְּזֵרָה שָׁוָה (מילה זהה בשני מקומות מלמדת זה על זה), "
        "קַל וָחֹמֶר, בִּנְיַן אָב, הֶקֵּשׁ, סְמוּכִים, ייתור או חיסור לשון, כלל ופרט, "
        "ומשמעות שמות וגימטריה. חפש חוט אחד אמיתי שמחבר את המקורות.\n"
        "אל תמציא ציטוטים — התבסס רק על הטקסטים והמילים המשותפות שסופקו."
    )
    user = ("המקורות של היום (יתכן ארמית — תרגם והבן):\n" + "\n".join(pool) +
            f"\n\nמילים משותפות שחושבו בקוד בין המקורות (רמז לגזרה שווה): {sw}\n"
            f"נושאים מימים קודמים (זיכרון משותף — להמשיך ולהעמיק): {pr}\n"
            f"קשרים מימים קודמים: {prl}\n\n"
            "מצא את החוט המקשר בדרך הגמרא. החזר JSON בלבד: {"
            '"theme":"נושא מרכזי אחד במשפט",'
            '"method":"באיזו מידה/דרך גמרא קישרת (למשל: גזרה שווה על המילה X)",'
            '"aramaic":"אם היה ארמית — תרגום קצר לעברית של הביטוי המרכזי, אחרת ריק",'
            '"links":[{"a":"מקור","b":"מקור","point":"הקשר ביניהם, בסגנון לימוד"}],'
            '"thread":"פסקה שמסבירה איך הכל מתחבר, כדרך הסוגיה"}')
    try:
        msg = client.messages.create(model=MODEL, max_tokens=1800, temperature=0.6,
                system=sysmsg, messages=[{"role":"user","content":user}])
        raw = "".join(b.text for b in msg.content if b.type=="text").strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)
    except Exception:
        return {"theme":"", "method":"", "aramaic":"", "links":[], "thread":""}

# ---------- סוכן מגיד (Claude): כותב את השיעור ----------
def write_lesson(daf, mishnayot, comms, parasha, extras, gem_lines, codes, conn, prior):
    client = anthropic.Anthropic()
    mish = "\n".join(f"[משנה {i+1} · {m['he_ref']}] {m['hebrew'][:900]}"
                     for i, m in enumerate(mishnayot)) or "(אין)"
    src  = "\n".join(f"[{c['name']}] {c['text'][:400]}" for c in comms[:10]) or "(אין)"
    par  = (f"פרשת השבוע ({parasha['he_ref']}):\n{parasha['hebrew'][:1400]}"
            if parasha else "(אין)")
    ext  = "\n".join(f"[{e['he_ref']}] {e['hebrew'][:900]}" for e in extras) or "(אין)"
    els_txt = "; ".join(f"{h['word']} (דילוג {h['skip']} {h['dir']})" for h in codes.get("els", [])) or "לא נמצאו"
    atb_txt = "; ".join(f"{a['word']}→{a['atbash']}" for a in codes.get("atbash", []))
    not_txt = f"ראשי תיבות: {codes.get('notarikon',{}).get('initials','')} · סופי תיבות: {codes.get('notarikon',{}).get('finals','')}"
    sysmsg = ("אתה רב שכותב שיעור יומי עשיר ומעמיק באורך עמוד מלא, בעברית בלבד, "
              "בטון חם, ברור ומאיר. אתה מלקט מן הדף, משתי המשניות היומיות, ממפרשים, "
              "מפרשת השבוע, ומכל מרחב המקורות (תנ\"ך, תהילים, נביאים, מדרש, זוהר). "
              "אם יש ארמית — תרגם לעברית. קשר בין המקורות בדרך לימוד הגמרא, "
              "שמוציאה מכל מילה ואות את משמעותהּ, וציין מאיפה כל מקור הובא. "
              "כתוב תוכן עשיר ומפורט ומקורי. אל תמציא ציטוטים מדויקים. "
              "לגבי גימטריה וצפנים: השתמש אך ורק בנתונים שסופקו לך (מחושבים בקוד) — "
              "אל תמציא מספרים, דילוגים או צפנים בעצמך. "
              "בקטע הצפנים הצג את הממצאים בענווה וביושר: כדבר ללימוד והתבוננות, "
              "תוך ציון מפורש שדפוסי דילוג מופיעים גם במקרה ובכל טקסט ארוך, ושאין בכך הוכחה.")
    chain = "; ".join(f"{c['name']} (מתוך {c.get('from','')})" for c in comms[:10]) or "אין"
    user = (f"הדף היומי: {daf['he_ref']}\nטקסט הדף (יתכן ארמית — תרגם והבן):\n{daf['hebrew'][:3200]}\n\n"
            f"שתי המשניות היומיות:\n{mish}\n\n"
            f"מפרשים מאושרים (לבסס עליהם את דברי המפרשים):\n{src}\n\n"
            f"{par}\n\n"
            f"מקורות נוספים מכל הספרים (תהילים ועוד):\n{ext}\n\n"
            f"שרשרת המקורות — מאיפה כל מקור הובא:\n{chain}\n\n"
            f"חוט מקשר שמצא סוכן המקשר (בדרך הגמרא) — בנה עליו את השיעור:\n"
            f"נושא: {conn.get('theme','')}\nדרך הלימוד: {conn.get('method','')}\n"
            f"תרגום מארמית: {conn.get('aramaic','')}\n"
            f"חיבור: {conn.get('thread','')}\n"
            f"קשרים: {'; '.join(l.get('point','') for l in conn.get('links',[]))}\n\n"
            f"נושאים מימים קודמים (להמשכיות — אפשר להתייחס): "
            f"{'; '.join(p.get('theme','') for p in prior if p.get('theme')) or 'אין'}\n\n"
            f"גימטריה מחושבת (ודאית — רק אלה מותר לצטט): {'; '.join(gem_lines) or 'אין'}\n\n"
            f"צפנים שחושבו בקוד מתוך טקסט התורה (רק אלה מותר להזכיר):\n"
            f"דילוגי אותיות: {els_txt}\nאתב\"ש: {atb_txt}\nנוטריקון: {not_txt}\n"
            f"(נסרקו {codes.get('letters_scanned',0)} אותיות)\n\n"
            "כתוב שיעור עמוד מלא ועשיר שמחבר בין כל המקורות סביב החוט המקשר, בדרך לימוד הגמרא "
            "(הוצאת משמעות מכל מילה ואות), עם תרגום כל ביטוי ארמי לעברית. "
            "החזר JSON תקין בלבד, ללא טקסט נוסף: {"
            '"title":"כותרת",'
            '"intro":"פתיחה של 2-3 משפטים עם החוט המקשר",'
            '"method":"דרך הלימוד שבה חוברו המקורות (מידת הדרש/היגיון הסוגיה), במשפט",'
            '"aramaic":"תרגום קצר לעברית של הביטוי הארמי המרכזי בדף, אם יש; אחרת ריק",'
            '"daf":{"ref":"מקור הדף","teaching":"ביאור עשיר של 4-6 משפטים"},'
            '"mishnayot":[{"ref":"מקור המשנה","text_summary":"תקציר במילים שלך","insight":"תובנה"}],'
            '"commentators":[{"name":"שם המפרש","point":"מה הוא אומר, במילים שלך"}],'
            '"connections":[{"source":"שם המקור","from":"מאיפה הובא","point":"הקשר"}],'
            '"deep_dive":"פסקת עיון לעומק (4-6 משפטים) שמחברת את הכל לרעיון אחד",'
            '"halacha":"נקודה הלכתית מעשית אחת היוצאת מן הסוגיה",'
            '"gematria_note":"רק מהמספרים שסופקו, אחרת ריק",'
            '"codes":"פסקת צפנים בתורה: תאר ביושר את הממצאים שסופקו (דילוגים/אתב\\"ש/נוטריקון), עם הסתייגות מפורשת שאינם הוכחה. אם לא נמצא דבר, כתוב זאת.",'
            '"question":"שאלה למחשבה","takeaway":"לקח ליום"}\n'
            "חשוב: כלול פסקה לכל אחת משתי המשניות, 3-5 מפרשים, ו-4-6 קשרים.")
    msg = client.messages.create(model=MODEL, max_tokens=8000, temperature=0.5,
            system=sysmsg, messages=[{"role":"user","content":user}])
    raw = "".join(b.text for b in msg.content if b.type=="text").strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(raw)
    except Exception:
        i, j = raw.find("{"), raw.rfind("}")    # חילוץ גוש ה-JSON אם נוסף טקסט מסביב
        if i != -1 and j != -1 and j > i:
            return json.loads(raw[i:j+1])
        raise

# ---------- שמירה ----------
def save(record):
    data = []
    if os.path.exists(OUT):
        try: data = json.load(open(OUT, encoding="utf-8"))
        except Exception: data = []
    data = [r for r in data if r.get("date") != record["date"]]
    data.insert(0, record)
    json.dump(data, open(OUT,"w",encoding="utf-8"), ensure_ascii=False, indent=2)

# ---------- שו"ת: שיעור לפי שאלה (סוכן על-פי שאילתא) ----------
QA_OUT = "qa.json"
def save_qa(record):
    data = []
    if os.path.exists(QA_OUT):
        try: data = json.load(open(QA_OUT, encoding="utf-8"))
        except Exception: data = []
    data.insert(0, record)
    json.dump(data[:300], open(QA_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

def answer_question(question, mode="full"):
    question = (question or "").strip()
    if not question:
        print("אין שאלה."); return
    if forbidden_hit(question):
        print("שאלה נחסמה (תוכן לא מתאים)."); return
    if not has_hebrew(question):
        print("נא לשאול בעברית."); return
    mode = "short" if str(mode).strip() in ("short", "תמצית", "קצר") else "full"
    client = anthropic.Anthropic()
    prior = load_insights(10)
    learned = "; ".join(p.get("theme","") for p in prior if p.get("theme")) or "אין"
    lib = []
    if os.path.exists(LIB_OUT):
        try: lib = [e["name"] for e in json.load(open(LIB_OUT, encoding="utf-8"))[:30]]
        except Exception: lib = []
    libs = ", ".join(lib) or "—"
    base = (
        "אתה רב ותלמיד חכם הכותב בעברית בלבד, בטון בהיר ומכבד, בדרך הלימוד של הסוגיה "
        "(גמרא, ראשונים, שולחן ערוך ונושאי כליו). כתוב תוכן מקורי במילים שלך — "
        "אל תמציא ציטוטים מדויקים או מראי-מקום שאינך בטוח בהם. "
        "חובה לסיים בהבהרה שזהו חומר לימוד בלבד ואינו פסק הלכה, ולהוראה למעשה יש לפנות לרב מוסמך."
    )
    if mode == "short":
        sysmsg = base + " כתוב תמצית קצרה וברורה."
        user = (f"השאלה/הנושא: {question}\n"
                f"נושאים שכבר נלמדו (אם רלוונטי): {learned}\n\n"
                "כתוב תמצית. החזר JSON תקין בלבד: {"
                '"title":"כותרת קצרה",'
                '"summary":"תמצית בהירה של 3-5 משפטים",'
                '"key_sources":["שמות 2-4 מקורות עיקריים"],'
                '"bottom_line":"שורה תחתונה לימודית במשפט",'
                '"disclaimer":"חומר לימוד בלבד, אינו פסק הלכה; להוראה למעשה יש לפנות לרב מוסמך"}')
        max_tok = 1800
    else:
        sysmsg = base + " כתוב שיעור שלם, עשיר ומסודר באורך עמוד."
        user = (f"השאלה/הנושא: {question}\n"
                f"נושאים שכבר נלמדו (זיכרון משותף — נצל אם רלוונטי): {learned}\n"
                f"מקורות בספרייה: {libs}\n\n"
                "כתוב שיעור שלם ועשיר. החזר JSON תקין בלבד: {"
                '"title":"כותרת",'
                '"question":"ניסוח השאלה/הנושא בבהירות",'
                '"intro":"פתיחה והגדרת הנושא",'
                '"sources":[{"name":"שם המקור (גמרא/רמב\\"ם/שו\\"ע וכו׳)","point":"מה עולה ממנו, במילים שלך"}],'
                '"discussion":"גוף הדיון ההלכתי — הדעות, הסברות, דרך הסוגיה (פסקה מפורטת)",'
                '"deep_dive":"עיון לעומק שמחבר את הרעיונות",'
                '"conclusion":"סיכום לימודי של העולה מן הדברים",'
                '"practical":"נקודות מעשיות עיקריות (כלליות)",'
                '"disclaimer":"חומר לימוד בלבד, אינו פסק הלכה; להוראה למעשה יש לפנות לרב מוסמך"}\n'
                "כלול 3-6 מקורות.")
        max_tok = 6000
    lesson = {}
    try:
        msg = client.messages.create(model=MODEL, max_tokens=max_tok, temperature=0.5,
                system=sysmsg, messages=[{"role":"user","content":user}])
        raw = "".join(b.text for b in msg.content if b.type=="text").strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            lesson = json.loads(raw)
        except Exception:
            i, j = raw.find("{"), raw.rfind("}")
            lesson = json.loads(raw[i:j+1]) if (i!=-1 and j>i) else {}
    except Exception as ex:
        print("⚠️ יצירת התשובה נכשלה:", ex)
        lesson = {"title": question[:60], "question": question,
                  "intro": "התשובה תיווצר שוב בקרוב.",
                  "disclaimer": "חומר לימוד בלבד, אינו פסק הלכה; יש לפנות לרב מוסמך.",
                  "_error": str(ex)[:200]}
    if forbidden_hit(json.dumps(lesson, ensure_ascii=False)):
        print("התשובה נחסמה."); return
    rec = {"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "question": question, "mode": mode, "lesson": lesson}
    save_qa(rec)
    print("✅ נשמרה תשובה לשו\"ת:", lesson.get("title",""))

# ---------- main ----------
def main():
    today = datetime.date.today()
    reason = resting_reason(today)
    if reason:
        print("🕎 הישיבה שובתת —", reason, "| לא נכתב שיעור."); return

    items = daily_items()
    daf_item = items.get("Daf Yomi")
    if not daf_item or not daf_item.get("ref"):
        print("⚠️ לא נמצא דף יומי."); sys.exit(1)

    daf = get_text(daf_item["ref"])
    if forbidden_hit(daf["hebrew"]):
        print("🛑 עמוד הדף הכיל שם חסום — המחקר הופסק, לא נשמר דבר."); return

    comms = get_commentaries(daf_item["ref"], cap=12)

    # שתי המשניות היומיות — בנפרד
    mishnayot = []
    my = items.get("Mishnah Yomi")
    if my and my.get("ref"):
        mt = get_text(my["ref"])
        if not forbidden_hit(mt["hebrew"]):
            segs = mt["segments"] or [mt["hebrew"]]
            for seg in segs[:2]:
                mishnayot.append({"he_ref": mt["he_ref"], "hebrew": seg})

    # פרשת השבוע — מקור נוסף לליקוט
    parasha = None
    parasha_name = ""
    par = items.get("Parashat Hashavua")
    if par and par.get("ref"):
        parasha_name = (par.get("displayValue") or {}).get("he") or ""
        try:
            pt = get_text(par["ref"])
            if not forbidden_hit(pt["hebrew"]):
                parasha = pt
        except Exception:
            parasha = None

    # מקורות נוספים מכל הספרים — תהילים של היום (מתחלף לפי התאריך)
    extras = []
    try:
        psalm_no = (today.day % 150) or 150
        ps = get_text(f"Psalms {psalm_no}")
        if ps["hebrew"] and not forbidden_hit(ps["hebrew"]):
            extras.append({"he_ref": ps["he_ref"], "hebrew": ps["hebrew"]})
    except Exception:
        pass

    gem_lines = []
    for v, words in list(equal_pairs(daf["hebrew"]).items())[:5]:
        gem_lines.append(f"{v}={'='.join(words)}")

    # צפנים — מבוססים על הטקסט המלא של חמישה חומשי תורה (לפי שבוע הפרשה)
    print("🔯 טוען את חמישה חומשי תורה לחיפוש צפנים…")
    torah_text = fetch_full_torah()
    codes_src = torah_text if torah_text else (parasha["hebrew"] if parasha else daf["hebrew"])
    codes = build_codes(codes_src)

    # פיד צפנים נפרד (codes.json): כל חמשת החומשים + פרשת השבוע
    try:
        rec = build_codes_record(today, torah_text or codes_src,
                                 parasha["hebrew"] if parasha else "", parasha_name)
        now = datetime.datetime.now()
        rec["ts"] = now.strftime("%Y-%m-%d %H:%M")
        rec["run"] = "בוקר" if now.hour < 12 else "אחר הצהריים"
        save_codes(rec)
        print("🔯 נשמרו צפנים ל-codes.json")
    except Exception as ex:
        print("⚠️ לא נשמרו צפנים:", ex)

    # סוכן בודק התפילות — אוסף מקורות נחלת-הכלל להשלמת החסר
    prayers_n = 0
    try:
        pr_rec = prayers_audit()
        prayers_n = len(pr_rec.get("prayers", {}))
        print("🕯️ נשמרו מקורות תפילה ל-prayers_audit.json")
    except Exception as ex:
        print("⚠️ לא נשמרו מקורות תפילה:", ex)

    # סוכן ספרן: עוקב אחר הציטוטים גם מפרשת השבוע, ומאחד לספרייה
    if parasha and par.get("ref"):
        try:
            for c in get_commentaries(par["ref"], cap=8):
                if c["name"] not in {x["name"] for x in comms}:
                    comms.append(c)
        except Exception:
            pass
    lib = update_library(comms, today.isoformat())
    library_total = len(lib)

    # מילים משותפות בין כל המקורות (חיבורים אמיתיים)
    pool = [("הדף", daf["hebrew"])]
    for m in mishnayot: pool.append(("משנה", m["hebrew"]))
    if parasha: pool.append(("פרשה", parasha["hebrew"]))
    for e in extras: pool.append((e["he_ref"], e["hebrew"]))
    for c in comms[:10]: pool.append((c["name"], c["text"]))
    shared = shared_words(pool)
    prior = load_insights()

    print("🔗 סוכן מקשר מחפש קשרים…")
    conn = connector_agent(daf, mishnayot, comms, parasha, extras, shared, prior)

    print("📖 סוכן מגיד כותב שיעור עבור", daf["he_ref"], "…")
    try:
        lesson = write_lesson(daf, mishnayot, comms, parasha, extras, gem_lines, codes, conn, prior)
    except Exception as ex:
        print("⚠️ המגיד נכשל (כנראה API/JSON) — נשמרים שאר הקבצים:", ex)
        lesson = {
            "title": f"שיעור {daf['he_ref']}",
            "intro": ("השיעור המלא ייכתב בריצה הבאה. בינתיים נאספו המקורות, "
                      "הצפנים, התפילות והקשרים."),
            "theme": conn.get("theme",""),
            "codes": "ראה כרטיס הצפנים.",
            "takeaway": conn.get("thread",""),
            "_error": str(ex)[:200],
        }

    # זיכרון משותף: שומרים את החוט, הקשרים, דרך הלימוד והתרגום — כדי שמחר יבנו עליו
    save_insight({"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                  "date": today.isoformat(), "ref": daf["he_ref"],
                  "theme": conn.get("theme",""), "thread": conn.get("thread",""),
                  "method": conn.get("method",""), "aramaic": conn.get("aramaic",""),
                  "links_pts": [l.get("point","") for l in conn.get("links",[])]})

    save({
        "date": today.isoformat(),
        "ref": daf["ref"], "he_ref": daf["he_ref"],
        "lesson": lesson,
        "theme": conn.get("theme",""),
        "shared_words": [w for w, _ in shared],
        "codes_raw": codes,
        "sources_used": [c["name"] for c in comms],
        "policy": {"strict": True, "hebrew_only": True},
    })
    # יומן הסוכנים — תיעוד מלא של מה שכל סוכן עשה בריצה הזו
    try:
        now = datetime.datetime.now()
        codes_n = len((codes or {}).get("els", []))
        append_log({
            "ts": now.strftime("%Y-%m-%d %H:%M"),
            "run": "בוקר" if now.hour < 12 else "אחר הצהריים",
            "date": today.isoformat(),
            "daf": daf["he_ref"],
            "librarian": {"sources_found": len(comms),
                          "sources": [c["name"] for c in comms[:12]],
                          "library_total": library_total},
            "connector": {"theme": conn.get("theme",""),
                          "method": conn.get("method",""),
                          "aramaic": conn.get("aramaic",""),
                          "links": len(conn.get("links",[]))},
            "teacher": {"title": lesson.get("title","")},
            "codes": {"found": codes_n},
            "prayers": {"audited": prayers_n},
            "shared_words": [w for w, _ in shared][:10],
        })
        print("📋 נשמר יומן הסוכנים ל-log.json")
    except Exception as ex:
        print("⚠️ לא נשמר יומן:", ex)

    print("✅ נשמר השיעור:", lesson.get("title",""))

if __name__ == "__main__":
    _q = os.environ.get("QA_QUESTION", "").strip()
    if _q:
        _body = os.environ.get("QA_BODY", "")
        _mode = "short" if ("[[short]]" in _body or "תמצית" in _body) else "full"
        answer_question(_q, _mode)
    else:
        main()
