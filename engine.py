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

def get_text(ref):
    d = requests.get(f"{SEF}/texts/{requests.utils.quote(ref)}", timeout=25,
                     params={"context":0,"pad":0}).json()
    segs = _clean(d.get("he", []))
    return {"ref": d.get("ref", ref), "he_ref": d.get("heRef", ref), "hebrew": " ".join(segs)}

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
def write_lesson(daf, mishnayot, comms, gem_lines):
    client = anthropic.Anthropic()
    mish = "\n".join(f"[{m['he_ref']}] {m['hebrew'][:700]}" for m in mishnayot) or "(אין)"
    src  = "\n".join(f"[{c['name']}] {c['text'][:300]}" for c in comms[:6]) or "(אין)"
    sysmsg = ("אתה רב שכותב שיעור יומי עשיר באורך עמוד, בעברית בלבד, בטון חם וברור. "
              "אתה מלקט מן הדף, משתי המשניות היומיות, וממקורות נוספים. אל תמציא ציטוטים.")
    user = (f"הדף היומי: {daf['he_ref']}\nטקסט הדף:\n{daf['hebrew'][:2500]}\n\n"
            f"שתי המשניות היומיות:\n{mish}\n\nמפרשים מאושרים:\n{src}\n\n"
            f"גימטריה מחושבת: {'; '.join(gem_lines) or 'אין'}\n\n"
            "החזר JSON תקין בלבד, ללא טקסט נוסף: {"
            '"title":"כותרת","intro":"פתיחה עם החוט המקשר",'
            '"daf":{"ref":"מקור הדף","teaching":"3-4 משפטים"},'
            '"mishnayot":[{"ref":"מקור","text_summary":"תקציר במילים שלך","insight":"תובנה"}],'
            '"connections":[{"source":"מקור","point":"קשר"}],'
            '"gematria_note":"הערה אם רלוונטי","question":"שאלה למחשבה","takeaway":"לקח ליום"}')
    msg = client.messages.create(model=MODEL, max_tokens=2200, temperature=0.5,
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

    comms = get_commentaries(daf_item["ref"])

    mishnayot = []
    my = items.get("Mishnah Yomi")
    if my and my.get("ref"):
        mt = get_text(my["ref"])
        if not forbidden_hit(mt["hebrew"]):
            mishnayot.append(mt)

    gem_lines = []
    for v, words in list(equal_pairs(daf["hebrew"]).items())[:4]:
        gem_lines.append(f"{v}={'='.join(words)}")

    print("📖 כותב שיעור עבור", daf["he_ref"], "…")
    lesson = write_lesson(daf, mishnayot, comms, gem_lines)

    save({
        "date": today.isoformat(),
        "ref": daf["ref"], "he_ref": daf["he_ref"],
        "lesson": lesson,
        "sources_used": [c["name"] for c in comms],
        "policy": {"strict": True, "hebrew_only": True},
    })
    print("✅ נשמר השיעור:", lesson.get("title",""))

if __name__ == "__main__":
    main()
