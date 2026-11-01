"""CAPTURE ONLY (R1601, Codex HOLD round 1 on PR #12): run the REAL benishchai_agent for the Ben Ish Chai dates 2026-08-09..13 with the
engine on main, record every raw model answer exactly as returned, and SAVE NOTHING (the archive save is replaced by a no-op that only
records what would have been saved). Output: bic_raw_captures.json (uploaded as a workflow artifact, also printed) + the measured
token usage (engine USAGE, PR #11). Lives only on the unmerged branch capture-bic-shoftim-r1601.   usage: python capture_bic_raw.py"""
import datetime, json, sys

import engine as E
import backfill_records as B

DATES = ["2026-08-09", "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"]
captures, calls = [], []
real_create = E.llm_create


def recording_create(client, **kw):
    msg = real_create(client, **kw)
    calls.append({"raw": "".join(b.text for b in msg.content if b.type == "text"),
                  "stop_reason": getattr(msg, "stop_reason", None),
                  "input_tokens": int(getattr(getattr(msg, "usage", None), "input_tokens", 0) or 0),
                  "output_tokens": int(getattr(getattr(msg, "usage", None), "output_tokens", 0) or 0),
                  "reask": kw["messages"][-1]["content"].endswith(E.REASK)})
    return msg


would_save = []
E.llm_create = recording_create
E._save_archive = lambda path, record, cap=400, check=None: would_save.append({"path": path, "record": record}) and False
if not E.preflight_sdk():
    sys.exit(1)
for d in DATES:
    day = datetime.date.fromisoformat(d)
    items = E.daily_items(day)
    parasha = B._par(items)[0]
    before = len(calls)
    try:
        rec = E.benishchai_agent(day, parasha)
        err = None
    except Exception as ex:                         # noqa: BLE001
        rec, err = None, repr(ex)[:300]
    mine = calls[before:]
    ws = would_save[-1]["record"] if would_save and would_save[-1]["record"].get("date") == d else None
    captures.append({"date": d, "parasha": parasha, "calls": mine, "agent_exception": err,
                     "would_have_saved_content": ws.get("content") if ws else None})
    print("CAPTURE %s parasha=%s calls=%d" % (d, parasha, len(mine)))
    for i, c in enumerate(mine):
        print("  RAW[%d] reask=%s stop=%s in=%d out=%d: %s" % (i, c["reask"], c["stop_reason"], c["input_tokens"], c["output_tokens"],
                                                            c["raw"].replace("\n", "\\n")))
json.dump({"engine_head": "main", "captured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "model": E.MODEL, "dates": captures}, open("bic_raw_captures.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(E.usage_summary())
print("SAVED_TO_ARCHIVE: none (the save was a no-op; %d record(s) would have been offered to it)" % len(would_save))
