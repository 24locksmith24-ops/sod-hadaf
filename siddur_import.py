"""Build the three siddur pages (shacharit.json, mincha.json, arvit.json) from the FULL text of Sefaria's "Siddur Edot HaMizrach" (R1116).

    python siddur_import.py            fetch from the Sefaria API and (re)write the three JSON files
    python siddur_import.py --check    fetch again and compare with the published files; exit 1 on any difference (the completeness check)

Every leaf of Sefaria's Weekday Shacharit / Weekday Mincha / Weekday Arvit becomes one page section, in Sefaria's order, with its own Sefaria ref.
Nothing is shortened: the whole Hebrew text of the leaf is kept (segments joined by newlines, HTML tags removed, text HTML-escaped because the page writes it
with innerHTML). Each section carries: n (Hebrew title), t (text), ref, segments, words, sha256 (of the text), so the page and the tests can prove completeness.

The previous pages were hand-written abridgments (placeholders such as "see the full text in the prayers card"); that text is replaced.
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
_NIK = re.compile(r"[֑-ׇ]")


def get_json(url, params=None, tries=4):
    last = None
    for i in range(1, tries + 1):
        try:
            r = requests.get(url, params=params, timeout=60, headers={"User-Agent": "sod-hadaf-siddur-import"})
            r.raise_for_status()
            return r.json()
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


def fetch_page(key):
    top_title, he_title, he_name = PAGES[key]
    idx = get_json("%s/index/%s" % (SEF, WORK.replace(" ", "_")))
    top = next(n for n in idx["schema"]["nodes"] if n.get("title") == top_title)
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
        sections.append({"n": "%d. %s" % (len(sections) + 1, he), "t": html.escape(raw_text, quote=False), "ref": ref,
                         "segments": len([l for l in raw_text.split("\n") if l.strip()]), "words": words(raw_text),
                         "sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest()})
        time.sleep(0.25)
    he_ver = next((v for v in (versions or []) if v.get("language") == "he"), {})
    he_ver = {k: (v.strip() if isinstance(v, str) else v) for k, v in he_ver.items()}
    return {
        "title": he_title,
        "subtitle": "נוסח עדות־המזרח · מלא, מתוך ספריא",
        "attribution": "%s · %s%s" % (WORK, he_ver.get("versionTitle", "Sefaria"), (" · " + he_ver["license"]) if he_ver.get("license") else ""),
        "note": "הטקסט המלא של הסידור מתוך ספריא, כל חלק עם הציון שלו (ref). נוסחי עדות שונים נבדלים — יש לבדוק מול סידור הקהילה.",
        "source": {"work": WORK, "node": top_title, "version": he_ver.get("versionTitle"), "license": he_ver.get("license"),
                   "versionSource": he_ver.get("versionSource"), "fetched_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "sections": sections,
    }


def write(key, page):
    with open(key + ".json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(page, f, ensure_ascii=False, indent=1)
        f.write("\n")


def compare(key, fresh):
    """-> list of differences between the published file and a fresh Sefaria fetch (empty = complete and identical)"""
    pub = json.load(open(key + ".json", encoding="utf-8"))
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
    rc = 0
    for key in PAGES:
        page = fetch_page(key)
        if "--check" in argv:
            d = compare(key, page)
            print(key, "OK: %d sections, %d words identical to Sefaria" % (len(page["sections"]), sum(s["words"] for s in page["sections"])) if not d else "DIFFERENT")
            for x in d:
                print("   -", x)
            rc = rc or (1 if d else 0)
        else:
            write(key, page)
            print("wrote %s.json: %d sections, %d words" % (key, len(page["sections"]), sum(s["words"] for s in page["sections"])))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
