"""Backfill of the daily ARCHIVE records (R1552): every saved record that is not complete -- a stub (_error), placeholder text
('...בריצה הבאה'), a blank field, or a Chok entry missing a category such as משנה/מוסר -- is regenerated COMPLETE with the fixed
engine, for ITS OWN date. Lessons (lessons/ + discoveries.json) are backfilled by backfill_lessons.py, not here.

    python backfill_records.py                         DRY RUN (the default): count and list the incomplete records; no network, no model
    python backfill_records.py --apply --limit 20      regenerate at most 20 records, oldest first
    python backfill_records.py --apply --archives chok,yerushalmi --from 2026-09-01 --to 2026-10-29

Never deletes. A record is replaced only by a COMPLETE one for the same date (_save_archive's date dedup); when the regeneration is
not complete, nothing is written, the old record stays, and the failure is recorded (exit 1). git history keeps every old record.
Prints the count of incomplete records per archive BEFORE and AFTER."""
import argparse
import datetime
import json
import os
import sys

import engine as E

# archive file -> (completeness rule, how to regenerate that record for date d from the date's calendar `items`)
def _par(items):
    par = items.get("Parashat Hashavua") or {}
    return (par.get("displayValue") or {}).get("he") or "", par.get("ref", "")


def _daf(items):
    ref = (items.get("Daf Yomi") or {}).get("ref")
    if not ref:
        return "", ""
    t = E.get_text(ref)
    return t.get("hebrew", ""), t.get("he_ref", "")


ARCHIVES = {
    "breslov":    (E.BRESLOV_OUT,    E.incomplete_content,    lambda d, it: E.breslov_agent(d)),
    "benishchai": (E.BIC_OUT,        E.incomplete_content,    lambda d, it: E.benishchai_agent(d, _par(it)[0])),
    "david":      (E.DAVID_OUT,      E.incomplete_content,    lambda d, it: E.david_agent(d)),
    "shlomo":     (E.SHLOMO_OUT,     E.incomplete_content,    lambda d, it: E.shlomo_agent(d)),
    "shnayim":    (E.SHNAYIM_OUT,    E.incomplete_content,    lambda d, it: E.shnayim_agent(d, *_par(it))),
    "hilulot":    (E.HILULOT_OUT,    E.incomplete_content,    lambda d, it: E.hilulot_agent(d)),
    "yerushalmi": (E.YERUSHALMI_OUT, E.incomplete_yerushalmi, lambda d, it: E.yerushalmi_agent(d, items=it)),
    "chok":       (E.CHOK_OUT,       E.incomplete_chok,       lambda d, it: E.chok_agent(d, *_par(it), *_daf(it), items=it)),
}


def load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return []


def incomplete(names, first=None, last=None):
    """-> {archive: [(date, reason), ...]} oldest first, within [first, last] when given"""
    out = {}
    for n in names:
        path, rule, _ = ARCHIVES[n]
        rows = []
        for r in load(path):
            d = r.get("date")
            if not d or (first and d < first) or (last and d > last):
                continue
            why = rule(r)
            if why:
                rows.append((d, why))
        out[n] = sorted(rows)
    return out


def counts(table):
    return {n: len(v) for n, v in table.items()}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="regenerate (costs model calls); without it: dry run")
    ap.add_argument("--limit", type=int, default=10, help="max records regenerated in this run")
    ap.add_argument("--archives", default=",".join(ARCHIVES), help="comma list of: " + ",".join(ARCHIVES))
    ap.add_argument("--from", dest="first")
    ap.add_argument("--to", dest="last")
    a = ap.parse_args(argv)
    names = [n.strip() for n in a.archives.split(",") if n.strip()]
    bad = [n for n in names if n not in ARCHIVES]
    if bad:
        print("unknown archive(s):", ", ".join(bad)); return 2
    before = incomplete(names, a.first, a.last)
    print("BEFORE: incomplete records per archive:", json.dumps(counts(before)), "total", sum(counts(before).values()))
    for n, rows in before.items():
        for d, why in rows:
            print("  %-10s %s  %s" % (n, d, why[:100]))
    if not a.apply:
        print("DRY RUN: nothing regenerated, nothing written.")
        return 0
    if not E.preflight_sdk():
        return 1
    todo = sorted((d, n) for n, rows in before.items() for d, _ in rows)[:a.limit]
    calendars, done, failed = {}, [], []
    for d, n in todo:
        day = datetime.date.fromisoformat(d)
        try:
            if d not in calendars:
                calendars[d] = E.daily_items(day)
            rec = ARCHIVES[n][2](day, calendars[d])
        except Exception as ex:                       # noqa: BLE001
            E.note_failure("backfill %s %s" % (n, d), ex)
            rec = None
        # success = the archive now holds a COMPLETE record for that date (the agent saved it); anything else = the old one stays
        now = next((r for r in load(ARCHIVES[n][0]) if r.get("date") == d), None)
        if rec and now is not None and not ARCHIVES[n][1](now):
            done.append((n, d)); print("  ✅ %s %s" % (n, d))
        else:
            failed.append((n, d)); print("  ❌ %s %s (old record kept)" % (n, d))
    after = incomplete(names, a.first, a.last)
    print("AFTER: incomplete records per archive:", json.dumps(counts(after)), "total", sum(counts(after).values()))
    print("backfill_records result: regenerated=%d failed=%d (limit %d)" % (len(done), len(failed), a.limit))
    return 1 if (failed or E.FAILURES) else 0


if __name__ == "__main__":
    sys.exit(main())
