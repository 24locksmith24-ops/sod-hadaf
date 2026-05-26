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
        seen.add(name); out.append({"name":name,"text":txt})
        if len(out) >= cap: break
    return out

# ---------- כתיבת השיעור (Claude) ----------
def write_lesson(daf, mishnayot, comms, parasha, extras, gem_lines, codes):
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
              "כתוב תוכן עשיר ומפורט. אל תמציא ציטוטים מדויקים. "
              "לגבי גימטריה וצפנים: השתמש אך ורק בנתונים שסופקו לך (מחושבים בקוד) — "
              "אל תמציא מספרים, דילוגים או צפנים בעצמך. "
              "בקטע הצפנים הצג את הממצאים בענווה וביושר: כדבר ללימוד והתבוננות, "
              "תוך ציון מפורש שדפוסי דילוג מופיעים גם במקרה ובכל טקסט ארוך, ושאין בכך הוכחה.")
    user = (f"הדף היומי: {daf['he_ref']}\nטקסט הדף:\n{daf['hebrew'][:2800]}\n\n"
            f"שתי המשניות היומיות:\n{mish}\n\n"
            f"מפרשים מאושרים (לבסס עליהם את דברי המפרשים):\n{src}\n\n"
            f"{par}\n\n"
            f"מקורות נוספים מכל הספרים (תהילים ועוד):\n{ext}\n\n"
            f"גימטריה מחושבת (ודאית — רק אלה מותר לצטט): {'; '.join(gem_lines) or 'אין'}\n\n"
            f"צפנים שחושבו בקוד מתוך טקסט התורה (רק אלה מותר להזכיר):\n"
            f"דילוגי אותיות: {els_txt}\nאתב\"ש: {atb_txt}\nנוטריקון: {not_txt}\n"
            f"(נסרקו {codes.get('letters_scanned',0)} אותיות מן הפרשה)\n\n"
            "כתוב שיעור עמוד מלא ועשיר. החזר JSON תקין בלבד, ללא טקסט נוסף: {"
            '"title":"כותרת",'
            '"intro":"פתיחה של 2-3 משפטים עם החוט המקשר",'
            '"daf":{"ref":"מקור הדף","teaching":"ביאור עשיר של 4-6 משפטים"},'
            '"mishnayot":[{"ref":"מקור המשנה","text_summary":"תקציר במילים שלך","insight":"תובנה"}],'
            '"commentators":[{"name":"שם המפרש","point":"מה הוא אומר, במילים שלך"}],'
            '"connections":[{"source":"שם המקור","point":"הקשר"}],'
            '"deep_dive":"פסקת עיון לעומק (4-6 משפטים) שמחברת את הכל לרעיון אחד",'
            '"halacha":"נקודה הלכתית מעשית אחת היוצאת מן הסוגיה",'
            '"gematria_note":"רק מהמספרים שסופקו, אחרת ריק",'
            '"codes":"פסקת צפנים בתורה: תאר ביושר את הממצאים שסופקו (דילוגים/אתב\\"ש/נוטריקון), עם הסתייגות מפורשת שאינם הוכחה. אם לא נמצא דבר, כתוב זאת.",'
            '"question":"שאלה למחשבה","takeaway":"לקח ליום"}\n'
            "חשוב: כלול פסקה לכל אחת משתי המשניות, 3-5 מפרשים, ו-4-6 קשרים.")
    msg = client.messages.create(model=MODEL, max_tokens=4200, temperature=0.5,
            system=sysmsg, messages=[{"role":"user","content":user}])
    raw = "".join(b.text for b in msg.content if b.type=="text").strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)

# ---------- שמירה ----------
def save(record):
    data = []
    if os.path.exists(OUT):
        try: data = json.load(open(OUT, encoding="utf-8"))
        except Exception: data = []
    data = [r for r in data if r.get("date") != record["date"]]
    data.insert(0, record)
    json.dump(data, open(OUT,"w",encoding="utf-8"), ensure_ascii=False, indent=2)

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
    par = items.get("Parashat Hashavua")
    if par and par.get("ref"):
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

    # צפנים בתורה — מחושב מטקסט הפרשה (תורה בלבד)
    # צפנים — מבוססים על הטקסט המלא של חמישה חומשי תורה (לפי שבוע הפרשה)
    print("🔯 טוען את חמישה חומשי תורה לחיפוש צפנים…")
    torah_text = fetch_full_torah()
    codes_src = torah_text if torah_text else (parasha["hebrew"] if parasha else daf["hebrew"])
    codes = build_codes(codes_src)

    print("📖 כותב שיעור עבור", daf["he_ref"], "…")
    lesson = write_lesson(daf, mishnayot, comms, parasha, extras, gem_lines, codes)

    save({
        "date": today.isoformat(),
        "ref": daf["ref"], "he_ref": daf["he_ref"],
        "lesson": lesson,
        "codes_raw": codes,
        "sources_used": [c["name"] for c in comms],
        "policy": {"strict": True, "hebrew_only": True},
    })
    print("✅ נשמר השיעור:", lesson.get("title",""))

if __name__ == "__main__":
    main()
