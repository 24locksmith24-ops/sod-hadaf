"""One tolerant parser for every model answer that should be JSON (R1526).

The model sometimes returns JSON that json.loads rejects:
  - a Hebrew abbreviation written with an ASCII double quote inside a string (רש"י, שו"ע) -> "Expecting ',' delimiter"
  - a missing comma between two members -> "Expecting ',' delimiter"
  - code fences or a sentence of prose around the object
  - an answer cut off at max_tokens -> "Unterminated string"
  - an empty answer -> "Expecting value: line 1 column 1"

parse_model_json() repairs only what can be repaired without losing or inventing content: stray quotes inside strings,
missing and trailing commas, fences and prose around ONE JSON value. It refuses (ModelJSONError) an answer that was cut
off, that holds more than one top-level JSON value, or that is empty. ask_json() then asks once more ("Return ONLY valid
JSON, no prose.") and raises ModelJSONError after the second failure, so the callers keep failing loudly: nothing
unparseable, partial or ambiguous is ever saved.
"""
import json
import re


class ModelJSONError(ValueError):
    pass


REASK = "Return ONLY valid JSON, no prose."
_FENCE = re.compile(r"```[A-Za-z0-9_-]*")
_SCALAR = re.compile(r"-?(?:\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|true|false|null")
_KEY_AHEAD = re.compile(r'\s*"[^"\n]{0,200}"\s*:')      # a quote followed by `"key":` closes the string before a missing comma


def _next_significant(s, k):
    while k < len(s) and s[k] in " \t\r\n":
        k += 1
    return s[k] if k < len(s) else ""


def repair(text, start=0):
    """Rewrite the JSON value that begins at text[start] ('{' or '[') into valid JSON: escape stray quotes inside strings,
    insert missing commas, drop trailing commas. -> (json_text, end) where text[end:] is whatever follows the value.
    Raises ModelJSONError if the text ends inside a string or an open container (cut off): that is never 'repaired'."""
    out, stack = [], []          # stack: per open container [kind, expecting]; expecting 'key'|'colon'|'value'|'sep'
    in_str, esc, role = False, False, None
    k, n = start, len(text)

    def done():
        if stack:
            stack[-1][1] = "sep"

    def need_comma():
        if stack and stack[-1][1] == "sep":           # a new member started where a ',' belongs
            out.append(",")
            stack[-1][1] = "key" if stack[-1][0] == "{" else "value"

    while k < n:
        c = text[k]
        if in_str:
            if esc:
                out.append(c); esc = False
            elif c == "\\":
                out.append(c); esc = True
            elif c == '"':
                nxt = _next_significant(text, k + 1)
                closes = (nxt in (",", "}", "]", "") or (role == "key" and nxt == ":")
                          or (role == "value" and _KEY_AHEAD.match(text, k + 1) is not None and stack and stack[-1][0] == "{"))
                if closes:
                    out.append(c); in_str = False
                    if role == "key":
                        stack[-1][1] = "colon"
                    else:
                        done()
                else:
                    out.append('\\"')                 # a quote inside the text, e.g. רש"י
            elif c == "\n":
                out.append("\\n")
            elif c == "\t":
                out.append("\\t")
            elif c != "\r":
                out.append(c)
            k += 1
            continue
        if c in " \t\r\n":
            out.append(c); k += 1; continue
        if c == '"':
            need_comma()
            role = "key" if stack and stack[-1][0] == "{" and stack[-1][1] == "key" else "value"
            in_str = True
            out.append(c); k += 1; continue
        if c in "{[":
            need_comma()
            stack.append([c, "key" if c == "{" else "value"])
            out.append(c); k += 1; continue
        if c in "}]":
            while out and out[-1] in " \t\r\n":
                out.pop()
            if out and out[-1] == ",":
                out.pop()                             # trailing comma
            if stack:
                stack.pop()
            out.append(c); k += 1
            if not stack:
                return "".join(out), k               # the top-level value is complete
            done(); continue
        if c == ",":
            if stack:
                stack[-1][1] = "key" if stack[-1][0] == "{" else "value"
            out.append(c); k += 1; continue
        if c == ":":
            if stack:
                stack[-1][1] = "value"
            out.append(c); k += 1; continue
        m = _SCALAR.match(text, k)
        if m:
            need_comma()
            out.append(m.group(0)); k = m.end()
            done(); continue
        out.append(c); k += 1                         # anything else is left for json.loads to reject
    raise ModelJSONError("answer was cut off (unterminated %s)" % ("string" if in_str else "object/array"))


def _single(rest):
    if "{" in rest or "[" in rest:
        raise ModelJSONError("more than one JSON value in the answer")


def parse_model_json(raw):
    """model text -> dict/list; raises ModelJSONError when the answer is empty, cut off, ambiguous or unrepairable"""
    text = _FENCE.sub("", raw or "").strip()
    if not text:
        raise ModelJSONError("empty answer")
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ModelJSONError("no JSON object or array in the answer")
    start = min(starts)
    try:
        value, end = json.JSONDecoder().raw_decode(text, start)     # valid JSON: no repair
    except ValueError as first:
        fixed, end = repair(text, start)                           # raises on a cut-off answer
        _single(text[end:])
        try:
            return json.loads(fixed)
        except ValueError as second:
            raise ModelJSONError("unparseable answer: %s (after repair: %s)" % (first, second)) from None
    _single(text[end:])
    return value


def answer_text(msg):
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def ask_json(create, **kw):
    """create(**kw) -> parsed JSON. One re-ask with REASK appended to the last user message when the first
    answer is not usable; raises ModelJSONError if the second answer is not usable either."""
    try:
        return parse_model_json(answer_text(create(**kw)))
    except ModelJSONError as first:
        msgs = [dict(m) for m in kw.get("messages", [])]
        if msgs and msgs[-1].get("role") == "user" and isinstance(msgs[-1].get("content"), str):
            msgs[-1]["content"] = msgs[-1]["content"] + "\n\n" + REASK
        else:
            msgs.append({"role": "user", "content": REASK})
        try:
            return parse_model_json(answer_text(create(**dict(kw, messages=msgs))))
        except ModelJSONError as second:
            raise ModelJSONError("%s; re-ask: %s" % (first, second)) from None
