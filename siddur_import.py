"""Build the siddur pages from the FULL text of Sefaria's "Siddur Edot HaMizrach" (R1116; all parts R2066).

    python siddur_import.py                    fetch from the Sefaria API and (re)write every page + siddur/index.json
    python siddur_import.py --only a,b         (re)write only the named page keys (+ siddur/index.json)
    python siddur_import.py --check            fetch again and compare with the published files; exit 1 on any difference (the completeness check)
    python siddur_import.py --snapshot         rewrite tests/fixtures/siddur_expected.json from the published files (no network)

    SIDDUR_RAW_DIR=dir   save every raw Sefaria API answer in dir (a pinned fixture);  SIDDUR_OFFLINE=1  read them back instead of the network

The three weekday pages keep their files (shacharit.json, mincha.json, arvit.json); every other top-level part of the Sefaria index is written to
siddur/<key>.json and listed, with the weekday pages, in siddur/index.json, which the page reads to build its menu.
Every leaf of a part becomes one page section, in Sefaria's order, with its own Sefaria ref.
Nothing is shortened: the whole Hebrew text of the leaf is kept (segments joined by newlines, HTML tags removed, text HTML-escaped because the page writes it
with innerHTML). Each section carries: n (Hebrew title), t (text), ref, segments, words, sha256 (of the text), so the page and the tests can prove completeness.
"""
import datetime
import hashlib
import html
import json
import os
import re
import sys
import time

import requests

SEF = "https://www.sefaria.org/api"
WORK = "Siddur Edot HaMizrach"
PAGES = {
    "shacharit": ("Weekday Shacharit", "סֵדֶר תְּפִלַּת שַׁחֲרִית", "שחרית"),
    "mincha": ("Weekday Mincha", "סֵדֶר תְּפִלַּת מִנְחָה", "מנחה"),
    "arvit": ("Weekday Arvit", "סֵדֶר תְּפִלַּת עַרְבִית", "ערבית"),
}
# key -> (Sefaria top-level node, menu group).  The page title is the node's own Hebrew title from Sefaria.  Order = Sefaria's order.
PARTS = {
    # R2066 batch 2: weekday additions, blessings, festivals, study
    "hashkamat_haboker": ("Preparatory Prayers", "weekday_more"),
    "tikkun_chatzot": ("The Midnight Rite", "weekday_more"),
    "shacharit_additions": ("Additions for Shacharit", "weekday_more"),
    "sefirat_haomer": ("Counting of the Omer", "festivals"),
    "birkat_halevana": ("Blessing of the Moon", "weekday_more"),
    "kriat_shema_al_hamita": ("Bedtime Shema", "weekday_more"),
    # R2066 batch 1: Shabbat
    "shabbat_candles": ("Shabbat Candle Lighting", "shabbat"),
    "shir_hashirim": ("Song of Songs", "shabbat"),
    "kabbalat_shabbat": ("Kabbalat Shabbat", "shabbat"),
    "shabbat_arvit": ("Shabbat Arvit", "shabbat"),
    "shabbat_evening": ("Shabbat Evening", "shabbat"),
    "shabbat_shacharit": ("Shabbat Shacharit", "shabbat"),
    "shabbat_mussaf": ("Shabbat Mussaf", "shabbat"),
    "shabbat_day_meal": ("Daytime Meal", "shabbat"),
    "shabbat_mincha": ("Shabbat Mincha", "shabbat"),
    "seuda_shlishit": ("Third Meal", "shabbat"),
    "havdalah": ("Havdalah", "shabbat"),
    # R2066 batch 2 (continued)
    "birkat_hamazon": ("Post Meal Blessing", "blessings"),
    "al_hamichya": ("Al Hamihya", "blessings"),
    "birkot_hanehenin": ("Blessings on Enjoyments", "blessings"),
    "rosh_chodesh": ("Rosh Hodesh", "festivals"),
    "shalosh_regalim": ("Prayers for Three Festivals", "festivals"),
    "chanukah": ("Hanukkah", "festivals"),
    "purim": ("Purim", "festivals"),
    "nissan": ("Nissan", "festivals"),
    "brachot_shonot": ("Assorted Blessings and Prayers", "blessings"),
    "taaniyot_vaavelut": ("Fast Days and Mourning", "festivals"),
    "mishnayot_leshabbat": ("Mishna Study for Shabbat", "study"),
}
GROUPS = {"weekday": "ימי חול", "weekday_more": "ימי חול — עוד", "shabbat": "שבת", "blessings": "ברכות", "festivals": "ראש חודש ומועדים", "study": "לימוד"}
_NIK = re.compile(r"[֑-ׇ]")


def _raw_path(url, params):
    key = url + "?" + json.dumps(params or {}, sort_keys=True)
    return os.path.join(os.environ["SIDDUR_RAW_DIR"], hashlib.sha256(key.encode("utf-8")).hexdigest()[:24] + ".json")


def get_json(url, params=None, tries=4):
    if os.environ.get("SIDDUR_RAW_DIR") and os.environ.get("SIDDUR_OFFLINE") == "1":
        with open(_raw_path(url, params), encoding="utf-8") as f:
            return json.load(f)["answer"]
    last = None
    for i in range(1, tries + 1):
        try:
            r = requests.get(url, params=params, timeout=60, headers={"User-Agent": "sod-hadaf-siddur-import"})
            r.raise_for_status()
            data = r.json()
            if os.environ.get("SIDDUR_RAW_DIR"):
                os.makedirs(os.environ["SIDDUR_RAW_DIR"], exist_ok=True)
                with open(_raw_path(url, params), "w", encoding="utf-8", newline="\n") as f:
                    json.dump({"url": url, "params": params, "answer": data}, f, ensure_ascii=False)
            return data
        except Exception as ex:                      # noqa: BLE001
            last = ex
            time.sleep(2 * i)
    raise RuntimeError("Sefaria request failed: %s (%s)" % (url, last))


def leaves(node, path=()):
    """-> [(title_en, title_he, [path titles])] of the leaf nodes under node, in schema order"""
    kids = node.get("nodes")
    t = node.get("title") or ""
    p = path + ((t,) if t else ())
    if kids:
        for k in kids:
            yield from leaves(k, p)
    else:
        he = next((x.get("text") for x in node.get("titles", []) if x.get("lang") == "he" and x.get("primary")), None) or node.get("heTitle") or t
        yield t, he, list(p)


def flatten(x, depth=0):
    """a Sefaria jagged array -> lines; sub-lists are separated by a blank line"""
    if isinstance(x, str):
        return [x]
    out = []
    for i, e in enumerate(x or []):
        part = flatten(e, depth + 1)
        if not part:
            continue
        if out and isinstance(e, list):
            out.append("")
        out.extend(part)
    return out


def clean_line(s):
    s = re.sub(r"<br\s*/?>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace(" ", " ")
    return re.sub(r"[ \t]+", " ", s).strip()


def section_text(raw_he):
    lines = [clean_line(l) for l in flatten(raw_he)]
    # collapse runs of blank lines, drop leading/trailing blanks
    out, blank = [], False
    for l in lines:
        if not l:
            if out and not blank:
                out.append("")
            blank = True
        else:
            out.append(l)
            blank = False
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def words(text):
    return len(re.findall(r"[א-ת]+", _NIK.sub("", text)))


def all_keys():
    return list(PAGES) + list(PARTS)


def path_of(key):
    """the published file of a page key: the weekday pages stay at the root, every other part lives in siddur/"""
    return key + ".json" if key in PAGES else os.path.join("siddur", key + ".json")


def _he_title(node):
    return next((x.get("text") for x in node.get("titles", []) if x.get("lang") == "he" and x.get("primary")), None) or node.get("heTitle") or node.get("title")


def fetch_page(key):
    idx = get_json("%s/index/%s" % (SEF, WORK.replace(" ", "_")))
    if key in PAGES:
        top_title, he_title, he_name = PAGES[key]
        top = next(n for n in idx["schema"]["nodes"] if n.get("title") == top_title)
    else:
        top_title = PARTS[key][0]
        top = next(n for n in idx["schema"]["nodes"] if n.get("title") == top_title)
        he_title = _he_title(top)
    sections, versions = [], None
    for en, he, path in leaves(top):
        ref = "%s, %s" % (WORK, ", ".join(path))
        d = get_json("%s/texts/%s" % (SEF, requests.utils.quote(ref)), {"context": 0, "pad": 0})
        text = section_text(d.get("he"))
        if not text:
            raise RuntimeError("empty Hebrew text for %s" % ref)
        if versions is None:
            versions = d.get("versions") or []
        raw_text = text
        sections.append({"n": "%d. %s" % (len(sections) + 1, html.escape(he, quote=False)), "t": html.escape(raw_text, quote=False), "ref": ref,
                         "segments": len([l for l in raw_text.split("\n") if l.strip()]), "words": words(raw_text),
                         "sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest()})
        if os.environ.get("SIDDUR_OFFLINE") != "1":
            time.sleep(0.25)
    he_ver = next((v for v in (versions or []) if v.get("language") == "he"), {})
    he_ver = {k: (v.strip() if isinstance(v, str) else v) for k, v in he_ver.items()}
    return {
        "title": html.escape(he_title, quote=False),
        "subtitle": "נוסח עדות־המזרח · מלא, מתוך ספריא",
        "attribution": "%s · %s%s" % (WORK, he_ver.get("versionTitle", "Sefaria"), (" · " + he_ver["license"]) if he_ver.get("license") else ""),
        "note": "הטקסט המלא של הסידור מתוך ספריא, כל חלק עם הציון שלו (ref). נוסחי עדות שונים נבדלים — יש לבדוק מול סידור הקהילה.",
        "source": {"work": WORK, "node": top_title, "version": he_ver.get("versionTitle"), "license": he_ver.get("license"),
                   "versionSource": he_ver.get("versionSource"), "fetched_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "sections": sections,
    }


def _dump(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")


def write(key, page):
    _dump(path_of(key), page)


def load_published(key):
    with open(path_of(key), encoding="utf-8") as f:
        return json.load(f)


def write_index():
    """siddur/index.json: the menu the page builds - every published part (Sefaria order within each group; menu groups in GROUPS order), with its file and group (built from the files, no network)"""
    parts = []
    for key in all_keys():
        page = load_published(key)
        parts.append({"key": key, "file": path_of(key).replace(os.sep, "/")[:-len(".json")], "title": page["title"],
                      "group": "weekday" if key in PAGES else PARTS[key][1], "node": page["source"]["node"],
                      "sections": len(page["sections"]), "words": sum(s["words"] for s in page["sections"])})
    _dump(os.path.join("siddur", "index.json"), {"schema": 1, "work": WORK, "groups": GROUPS, "parts": parts})


def write_snapshot():
    """tests/fixtures/siddur_expected.json from the published files (refs, words, segments, hashes) - the offline completeness snapshot"""
    exp = {}
    for key in all_keys():
        page = load_published(key)
        exp[key] = {"source": page["source"].get("version"),
                    "sections": [{"ref": s["ref"], "words": s["words"], "segments": s["segments"], "sha256": s["sha256"]} for s in page["sections"]]}
    _dump(os.path.join("tests", "fixtures", "siddur_expected.json"), exp)


def compare(key, fresh):
    """-> list of differences between the published file and a fresh Sefaria fetch (empty = complete and identical)"""
    if not os.path.exists(path_of(key)):
        return ["%s is missing" % path_of(key)]
    pub = load_published(key)
    diffs = []
    p_refs = [s.get("ref") for s in pub["sections"]]
    f_refs = [s["ref"] for s in fresh["sections"]]
    if p_refs != f_refs:
        diffs.append("section refs differ: published-only=%s sefaria-only=%s" % ([r for r in p_refs if r not in f_refs], [r for r in f_refs if r not in p_refs]))
    for ps, fs in zip(pub["sections"], fresh["sections"]):
        if ps.get("ref") == fs["ref"] and ps.get("sha256") != fs["sha256"]:
            diffs.append("%s: text differs (published %d words, Sefaria %d words)" % (fs["ref"], ps.get("words", -1), fs["words"]))
    return diffs


def main(argv):
    if "--snapshot" in argv:
        write_snapshot()
        print("wrote tests/fixtures/siddur_expected.json for %d pages" % len(all_keys()))
        return 0
    keys = all_keys()
    if "--only" in argv:
        keys = [k for k in argv[argv.index("--only") + 1].split(",") if k]
        unknown = [k for k in keys if k not in all_keys()]
        if unknown:
            print("unknown page keys:", unknown)
            return 2
    rc = 0
    for key in keys:
        page = fetch_page(key)
        if "--check" in argv:
            d = compare(key, page)
            print(key, "OK: %d sections, %d words identical to Sefaria" % (len(page["sections"]), sum(s["words"] for s in page["sections"])) if not d else "DIFFERENT")
            for x in d:
                print("   -", x)
            rc = rc or (1 if d else 0)
        else:
            write(key, page)
            print("wrote %s: %d sections, %d words" % (path_of(key), len(page["sections"]), sum(s["words"] for s in page["sections"])))
    if "--check" not in argv:
        write_index()
        print("wrote siddur/index.json")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
