"""Sifria: one PERMANENT file per daily lesson, plus an index by masechet and daf (R1116).

Layout (all under lessons/):
    lessons/<Masechet>/<DDD>.json      the lesson of that daf, e.g. lessons/Bekhorot/033.json   (created with O_EXCL: it can never be overwritten)
    lessons/<Masechet>/<DDD>.rN.json   a deliberate re-generation (N >= 2); the original stays
    lessons/index.json                 {"masechtot": {"Bekhorot": {"he": "בכורות", "dafim": {"33": {"file", "date", "title", "revisions": [...]}}}}}
                                       DERIVED from the files and rebuilt atomically (temp file + rename) each time; the only mutable file here.

Only a FULL lesson is stored. A stub (anything with "_error") is refused: it is a failure, not a lesson.
discoveries.json (what the page reads today) stays as the by-date VIEW; sync_view() adds a lesson to it but never replaces a full entry.
"""
import datetime
import hashlib
import json
import os
import re

ROOT = "lessons"
SCHEMA = 1


class StubRefused(ValueError):
    pass


def parse_ref(ref):
    """'Bekhorot 33' -> ('Bekhorot', 33); 'Bekhorot 33a' -> ('Bekhorot', 33)"""
    m = re.match(r"^\s*([A-Za-z' ]+?)\s+(\d+)[ab]?\s*$", ref or "")
    if not m:
        raise ValueError("cannot read a masechet and daf from %r" % ref)
    return m.group(1).strip().replace(" ", "_"), int(m.group(2))


def masechet_he(he_ref):
    parts = (he_ref or "").split()
    return " ".join(parts[:-1]) if len(parts) > 1 else (he_ref or "")


def path_for(ref, revision=1, root=ROOT):
    mas, daf = parse_ref(ref)
    name = "%03d.json" % daf if revision == 1 else "%03d.r%d.json" % (daf, revision)
    return os.path.join(root, mas, name)


def is_full(lesson):
    return isinstance(lesson, dict) and not lesson.get("_error") and len(json.dumps(lesson, ensure_ascii=False)) > 1000


def exists(ref, root=ROOT):
    p = path_for(ref, 1, root)
    if not os.path.exists(p):
        return False
    try:
        return is_full(json.load(open(p, encoding="utf-8")).get("lesson"))
    except (OSError, ValueError):
        return False


def _atomic_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def save_lesson(record, root=ROOT, revise=False):
    """record = the discoveries.json entry {date, ref, he_ref, lesson, theme, shared_words, codes_raw, sources_used, policy}.
    -> ('created'|'exists'|'revision', path). Never overwrites a lesson file. Raises StubRefused for a stub."""
    if not is_full(record.get("lesson")):
        raise StubRefused("not a full lesson: %s" % ((record.get("lesson") or {}).get("_error") or "too short"))
    first = path_for(record["ref"], 1, root)
    rev = 1
    if os.path.exists(first):
        if not revise:
            return "exists", first
        rev = 2
        while os.path.exists(path_for(record["ref"], rev, root)):
            rev += 1
    path = path_for(record["ref"], rev, root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = dict(record)
    body.update({"schema": SCHEMA, "stored_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "revision": rev,
                 "lesson_sha256": hashlib.sha256(json.dumps(record["lesson"], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()})
    with open(path, "x", encoding="utf-8") as f:          # exclusive create: a second writer fails instead of overwriting
        json.dump(body, f, ensure_ascii=False, indent=2)
    rebuild_index(root)
    return ("created" if rev == 1 else "revision"), path


def rebuild_index(root=ROOT):
    idx = {"schema": SCHEMA, "generated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "masechtot": {}}
    if os.path.isdir(root):
        for mas in sorted(os.listdir(root)):
            d = os.path.join(root, mas)
            if not os.path.isdir(d):
                continue
            dafim = {}
            for fn in sorted(os.listdir(d)):
                m = re.match(r"^(\d{3})(?:\.r(\d+))?\.json$", fn)
                if not m:
                    continue
                try:
                    rec = json.load(open(os.path.join(d, fn), encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                key = str(int(m.group(1)))
                e = dafim.setdefault(key, {"file": None, "date": None, "title": None, "he_ref": None, "revisions": []})
                rel = (root + "/" + mas + "/" + fn)
                if m.group(2) is None:
                    e.update({"file": rel, "date": rec.get("date"), "title": (rec.get("lesson") or {}).get("title"), "he_ref": rec.get("he_ref")})
                else:
                    e["revisions"].append(rel)
            if dafim:
                he = masechet_he(next((v["he_ref"] for v in dafim.values() if v["he_ref"]), ""))
                idx["masechtot"][mas] = {"he": he, "count": len(dafim), "dafim": dict(sorted(dafim.items(), key=lambda kv: int(kv[0])))}
    _atomic_json(os.path.join(root, "index.json"), idx)
    return idx


def import_from_view(view_path="discoveries.json", root=ROOT):
    """one-way, idempotent: every FULL lesson in the by-date view that has no file yet gets its permanent file. -> list of created paths"""
    created = []
    try:
        view = json.load(open(view_path, encoding="utf-8"))
    except (OSError, ValueError):
        return created
    for rec in reversed(view):                  # oldest first
        if is_full(rec.get("lesson")) and rec.get("ref"):
            try:
                st, p = save_lesson(rec, root)
            except (StubRefused, ValueError):
                continue
            if st == "created":
                created.append(p)
    return created


def sync_view(record, view_path="discoveries.json"):
    """add a lesson to the by-date view the page reads. A FULL entry already there for that date is never replaced; a stub for that date is."""
    try:
        data = json.load(open(view_path, encoding="utf-8"))
    except (OSError, ValueError):
        data = []
    for r in data:
        if r.get("date") == record["date"] and is_full(r.get("lesson")):
            return False
    data = [r for r in data if r.get("date") != record["date"]]
    data.append(record)
    data.sort(key=lambda r: r.get("date", ""), reverse=True)
    _atomic_json(view_path, data)
    return True


if __name__ == "__main__":
    import sys
    if "--migrate" in sys.argv:
        made = import_from_view()
        idx = rebuild_index()
        print("created %d lesson files; index: %s" % (len(made), {k: v["count"] for k, v in idx["masechtot"].items()}))
