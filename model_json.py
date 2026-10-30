"""One tolerant parser for every model answer that should be JSON (R1526).

The model sometimes returns JSON that json.loads rejects:
  - a Hebrew abbreviation written with an ASCII double quote inside a string (רש"י, שו"ע) -> "Expecting ',' delimiter"
  - an answer cut off at max_tokens -> "Unterminated string"
  - code fences or a sentence of prose around the object
  - an empty answer -> "Expecting value: line 1 column 1"

parse_model_json() fixes what can be fixed without inventing content. ask_json() asks once more
("Return ONLY valid JSON, no prose.") when even that fails, and raises ModelJSONError after the second
failure, so the callers keep failing loudly: nothing unparseable is ever saved.
"""
import json
import re


class ModelJSONError(ValueError):
    pass


REASK = "Return ONLY valid JSON, no prose."
_FENCE = re.compile(r"```[A-Za-z0-9_-]*")


def _outermost(raw):
    """the text from the first '{' or '[' to its matching close, or to the end when the answer was cut off"""
    starts = [i for i in (raw.find("{"), raw.find("[")) if i != -1]
    if not starts:
        raise ModelJSONError("no JSON object or array in the answer")
    start = min(starts)
    depth, in_str, esc = 0, False, False
    for k in range(start, len(raw)):
        c = raw[k]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return raw[start:k + 1]
    return raw[start:]


def _next_significant(s, k):
    while k < len(s) and s[k] in " \t\r\n":
        k += 1
    return s[k] if k < len(s) else ""


def repair(text):
    """Rewrite near-JSON into JSON: escape stray quotes inside strings, drop trailing commas, and close an
    answer that was cut off at the last complete value. Never adds keys or values that were not in the text."""
    out = []
    stack = []            # per open container: [kind, expecting]  kind '{' or '['; expecting 'key'|'value'|'sep'
    in_str, esc, str_role = False, False, None
    safe = None           # (len(out), stack snapshot) after the last complete value inside a container

    def mark():
        nonlocal safe
        safe = (len(out), [list(x) for x in stack])

    def value_done():
        if stack:
            stack[-1][1] = "sep"
            mark()

    for k, c in enumerate(text):
        if in_str:
            if esc:
                out.append(c); esc = False
            elif c == "\\":
                out.append(c); esc = True
            elif c == '"':
                nxt = _next_significant(text, k + 1)
                closes = nxt in (",", "}", "]", "") or (str_role == "key" and nxt == ":")
                if closes:
                    out.append(c); in_str = False
                    if str_role == "key":
                        stack[-1][1] = "colon"
                    else:
                        value_done()
                else:
                    out.append('\\"')          # a quote inside the text, e.g. רש"י
            elif c == "\n":
                out.append("\\n")
            elif c == "\t":
                out.append("\\t")
            elif c == "\r":
                continue
            else:
                out.append(c)
            continue
        if c in " \t\r\n":
            out.append(c); continue
        if c == '"':
            str_role = "key" if stack and stack[-1][0] == "{" and stack[-1][1] == "key" else "value"
            in_str = True
            out.append(c); continue
        if c in "{[":
            stack.append([c, "key" if c == "{" else "value"])
            out.append(c); continue
        if c in "}]":
            while out and out[-1] in " \t\r\n":
                out.pop()
            if out and out[-1] == ",":
                out.pop()                       # trailing comma
            if stack:
                stack.pop()
            out.append(c)
            value_done(); continue
        if c == ",":
            if stack:
                stack[-1][1] = "key" if stack[-1][0] == "{" else "value"
            out.append(c); continue
        if c == ":":
            if stack:
                stack[-1][1] = "value"
            out.append(c); continue
        out.append(c)                           # numbers, true/false/null
        if stack and k + 1 < len(text) and _next_significant(text, k + 1) in (",", "}", "]"):
            value_done()

    if in_str or stack:                         # cut off: keep everything up to the last complete value
        if safe is None:
            raise ModelJSONError("answer was cut off before the first complete value")
        n, st = safe
        del out[n:]
        while out and out[-1] in " \t\r\n,":
            out.pop()
        for kind, _ in reversed(st):
            out.append("}" if kind == "{" else "]")
    return "".join(out)


def parse_model_json(raw):
    """model text -> dict/list; raises ModelJSONError when nothing valid can be recovered"""
    text = _FENCE.sub("", raw or "").strip()
    if not text:
        raise ModelJSONError("empty answer")
    body = _outermost(text)
    try:
        return json.loads(body)
    except ValueError as first:
        try:
            return json.loads(repair(body))
        except ValueError as second:
            raise ModelJSONError("unparseable answer: %s (after repair: %s)" % (first, second)) from None


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
