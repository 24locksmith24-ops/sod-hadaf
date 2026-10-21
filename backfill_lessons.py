"""Backfill (R1116): write the lessons that are missing or are stubs, into Sifria (permanent files) and the by-date view.

    python backfill_lessons.py --dry-run                 list what would be written, change nothing (no model call)
    python backfill_lessons.py --limit 10                write at most 10 lessons (oldest first)
    python backfill_lessons.py --from 2026-08-20 --to 2026-10-20

A date is a target when it is not Friday/Saturday/a holiday (the yeshiva rests, as in engine.py), has no FULL lesson yet, and its daf is not already stored.
A daf the whole-word filter still blocks is reported as BLOCKED and skipped. Exit code 1 if any lesson failed (loud); successes are kept.
"""
import argparse
import datetime
import json
import os
import sys

import engine as E
import lessons_store as LS


def daterange(a, b):
    d = a
    while d <= b:
        yield d
        d += datetime.timedelta(days=1)


def load_view(path=None):
    try:
        return json.load(open(path or E.OUT, encoding="utf-8"))
    except (OSError, ValueError):
        return []


def targets(first, last, view, resting=None):
    """-> [(date, reason)] oldest first; reason is 'stub' (a stub is in the view for that date) or 'missing' (no entry at all)"""
    resting = resting or E.resting_reason
    full = {r["date"] for r in view if LS.is_full(r.get("lesson"))}
    stubs = {r["date"] for r in view if r.get("date") and not LS.is_full(r.get("lesson"))}
    out = []
    for d in daterange(first, last):
        iso = d.isoformat()
        if iso in full or d.weekday() in (4, 5):
            continue
        if resting(d):
            continue
        out.append((d, "stub" if iso in stubs else "missing"))
    return out


def prepare(d, items, torah_text):
    """the same sources main() collects for 'today', for the day d. -> dict or ('BLOCKED', hit) / ('NO_DAF', None)"""
    daf_item = items.get("Daf Yomi")
    if not daf_item or not daf_item.get("ref"):
        return ("NO_DAF", None)
    daf = E.get_text(daf_item["ref"])
    hit = E.forbidden_hit(daf["hebrew"])
    if hit:
        return ("BLOCKED", hit)
    comms = E.get_commentaries(daf_item["ref"], cap=12)
    mishnayot = []
    my = items.get("Mishnah Yomi")
    if my and my.get("ref"):
        mt = E.get_text(my["ref"])
        if not E.forbidden_hit(mt["hebrew"]):
            for seg in (mt["segments"] or [mt["hebrew"]])[:2]:
                mishnayot.append({"he_ref": mt["he_ref"], "hebrew": seg})
    parasha = None
    par = items.get("Parashat Hashavua")
    if par and par.get("ref"):
        try:
            pt = E.get_text(par["ref"])
            if not E.forbidden_hit(pt["hebrew"]):
                parasha = pt
        except Exception:
            parasha = None
    extras = []
    try:
        ps = E.get_text("Psalms %d" % ((d.day % 150) or 150))
        if ps["hebrew"] and not E.forbidden_hit(ps["hebrew"]):
            extras.append({"he_ref": ps["he_ref"], "hebrew": ps["hebrew"]})
    except Exception:
        pass
    gem_lines = ["%s=%s" % (v, "=".join(w)) for v, w in list(E.equal_pairs(daf["hebrew"]).items())[:5]]
    codes = E.build_codes(torah_text or (parasha["hebrew"] if parasha else daf["hebrew"]))
    pool = [("הדף", daf["hebrew"])] + [("משנה", m["hebrew"]) for m in mishnayot] + ([("פרשה", parasha["hebrew"])] if parasha else [])
    pool += [(e["he_ref"], e["hebrew"]) for e in extras] + [(c["name"], c["text"]) for c in comms[:10]]
    return {"daf": daf, "comms": comms, "mishnayot": mishnayot, "parasha": parasha, "extras": extras, "gem_lines": gem_lines,
            "codes": codes, "shared": E.shared_words(pool)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="first")
    ap.add_argument("--to", dest="last")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    view = load_view()
    dates = sorted(r["date"] for r in view if r.get("date"))
    first = datetime.date.fromisoformat(a.first) if a.first else datetime.date.fromisoformat(dates[0])
    last = datetime.date.fromisoformat(a.last) if a.last else datetime.date.today() - datetime.timedelta(days=1)
    for p in LS.import_from_view(E.OUT):
        print("📚 Sifria: permanent file created for an existing lesson:", p)
    todo = targets(first, last, view)
    print("backfill: %d candidate dates between %s and %s (%d stubs, %d with no entry)" %
          (len(todo), first, last, sum(1 for _, r in todo if r == "stub"), sum(1 for _, r in todo if r == "missing")))
    if not a.dry_run and not E.preflight_sdk():
        return 1
    torah_text = None
    done, blocked, failed, skipped = [], [], [], []
    for d, reason in todo:
        try:
            items = E.daily_items(d)
        except Exception as ex:                       # noqa: BLE001
            failed.append((d.isoformat(), "?", "calendar unreadable: %s" % ex)); continue
        ref = (items.get("Daf Yomi") or {}).get("ref", "?")
        if ref != "?" and LS.exists(ref):
            skipped.append((d.isoformat(), ref, "already stored")); continue
        if a.dry_run:
            print("  would write %s  %-14s (%s)" % (d, ref, reason)); continue
        if len(done) >= a.limit:
            print("limit reached (%d)" % a.limit); break
        if torah_text is None:
            torah_text = E.fetch_full_torah()
        prep = prepare(d, items, torah_text)
        if isinstance(prep, tuple):
            (blocked if prep[0] == "BLOCKED" else failed).append((d.isoformat(), ref, prep[0] + " " + str(prep[1]))); continue
        lesson, conn = E.generate_lesson(prep["daf"], prep["mishnayot"], prep["comms"], prep["parasha"], prep["extras"], prep["gem_lines"],
                                         prep["codes"], E.load_insights(), prep["shared"])
        rec = {"date": d.isoformat(), "ref": prep["daf"]["ref"], "he_ref": prep["daf"]["he_ref"], "lesson": lesson, "theme": conn.get("theme", ""),
               "shared_words": [w for w, _ in prep["shared"]], "codes_raw": prep["codes"], "sources_used": [c["name"] for c in prep["comms"]],
               "policy": {"strict": True, "hebrew_only": True}, "backfilled": True}
        if E.save(rec):
            done.append((d.isoformat(), ref))
            print("  ✅ %s %s" % (d, ref))
        else:
            failed.append((d.isoformat(), ref, "generation failed"))
    print("backfill result: written=%d blocked=%d failed=%d skipped=%d" % (len(done), len(blocked), len(failed), len(skipped)))
    for x in blocked:
        print("  BLOCKED", *x)
    for x in failed:
        print("  FAILED ", *x)
    return 1 if (failed or E.FAILURES) else 0


if __name__ == "__main__":
    sys.exit(main())
