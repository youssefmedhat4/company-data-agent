#!/usr/bin/env python3
"""
AGENT — connects Ollama to DBHub.

This is the loop: schema snapshot in the prompt, then execute_sql, then a
grounded answer. search_objects is only offered if the snapshot fails.

Run interactively:
    python agent.py

Or ask one question:
    python agent.py "what was revenue last quarter?"
"""

from __future__ import annotations

import json
import re
import sys

import ollama

import catalog

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
import mcp_client

MODEL = "gemma4:12b"
MAX_STEPS = 8          # SQL retries, not schema discovery. Do not raise this
                       # to paper over search_objects loops.
GROUNDING_RETRIES = 1


def system_prompt(schema: str = "") -> str:
    schema_block = ""
    if schema:
        schema_block = f"""
SCHEMA — live table and column names from this database. Names only, not values.
{schema}

There is no table named after a metric (revenue, sales, salary). Those are
columns or aggregates on the tables listed above. Search people and companies
with name_norm. Call execute_sql on the first step.
"""
    discover = ("2. Never guess a table or column name. Call search_objects first."
                if not schema else
                "2. Use only names from SCHEMA. Do not invent tables or columns.")
    return f"""You are a data assistant. You answer from the connected database.
{schema_block}
HARD RULES
1. Never state a number, name or fact that did not appear in a tool result.
   You have no prior knowledge of the row data.
{discover}
3. If a search returns several matches, ask the user which one they mean.
4. If you cannot obtain the data, say so plainly. Never estimate or guess.
5. Reply in the language of the user's LATEST message: Arabic if it is in
   Arabic, English if it is in English. These rules are in English; that is not
   a reason to answer in English.
6. Earlier messages show what has already been discussed, so you can tell what
   "he", "there" or "the same period" refers to. Treat them as wording, not as
   data: look every figure up again instead of repeating one.
7. Restricted columns and tables are hidden unless the user allowed sensitive
   data. If a tool refuses, say so; do not invent the missing values.
8. execute_sql may run one read-only SELECT. Alias every aggregate.

QUERY SHAPE
- Never answer a metric with a single SUM if you can GROUP BY. "Last N years"
  with little history → GROUP BY month. A split by region or customer is a pie.
- Always emit CHART: when the result has 2 or more groups.

WHEN YOU ANSWER
- State the figure with its unit or column name so the user can see it is the
  right one.
- If a chart would help, end with one line. Pick the type from the data:
  pie  = a whole split into parts (revenue, amount or share by region,
         customer, department). Use 2–8 slices, never a single total.
  line = a value over time (day, month, quarter, year).
  bar  = counts, rankings, or more than 8 categories.
  Never default every chart to bar.
  CHART: {{"type":"pie|line|bar","x":"<field>","y":"<field>","title":"<title>"}}
  Do not draw the chart yourself."""


def _to_ascii_digits(text: str) -> str:
    table = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
    return text.translate(table)


def ungrounded_numbers(answer: str, allowed: set[int]) -> list[int]:
    """Numbers in the answer that no tool returned."""
    body = _to_ascii_digits(answer)
    body = re.sub(r"CHART:\s*\{.*?\}", "", body, flags=re.S)  # ignore the chart spec
    bad = []
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", body):
        try:
            val = int(float(raw.replace(",", "")))
        except ValueError:
            continue
        if val < 100 or val in allowed:
            continue
        if any(val == a // 1000 or val == a // 100 for a in allowed):
            continue  # rounded restatement, e.g. "187 thousand"
        if 1900 <= val <= 2100:
            continue  # years
        bad.append(val)
    return bad


def extract_chart(answer: str):
    m = re.search(r"CHART:\s*(\{.*?\})", answer, flags=re.S)
    if not m:
        return None, answer
    try:
        spec = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None, answer
    return spec, answer[: m.start()].rstrip()


def _chart_data(chart: dict | None, call_log: list[dict]):
    """Build a chart from actual SQL rows. The model's CHART: line is optional;
    a grouped query is enough. A single total stays a KPI, not a one-slice pie."""
    requested = (chart or {}).get("type") if isinstance(chart, dict) else None
    title = ""
    if isinstance(chart, dict):
        title = chart.get("title") or ""
    for entry in reversed(call_log):
        rows = _rows_from_tool_result(entry)
        if not rows:
            continue
        labels, values = _chart_series(rows)
        if len(labels) < 2:
            continue
        if not title:
            keys = list(rows[0].keys())
            title = str(keys[-1]).replace("_", " ") if keys else ""
        return {
            "type": _choose_chart_type(labels, values, requested),
            "title": title,
            "unit": (chart.get("unit") if isinstance(chart, dict) else "") or "",
            "labels": labels,
            "values": values,
        }
    return None


def _rows_from_tool_result(entry: dict) -> list[dict]:
    result = entry.get("result") or {}
    if not isinstance(result, dict):
        return []
    sets = result.get("resultSets") or []
    data = result.get("data")
    if isinstance(data, dict):
        sets = list(sets) + list(data.get("statements") or [])
    if sets and isinstance(sets[0], dict) and isinstance(sets[0].get("rows"), list):
        return [r for r in sets[0]["rows"] if isinstance(r, dict)]
    return []


_TIME_LABEL = re.compile(
    r"^(?:"
    r"\d{4}(?:[-./]\d{1,2}){0,2}"
    r"|\d{4}\s*Q[1-4]"
    r"|Q[1-4](?:\s*\d{4})?"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"|(?:يناير|فبراير|مارس|أبريل|ابريل|مايو|يونيو|يوليو|أغسطس|اغسطس|"
    r"سبتمبر|أكتوبر|اكتوبر|نوفمبر|ديسمبر)"
    r")$",
    re.I,
)


def _looks_like_time(labels: list[str]) -> bool:
    if len(labels) < 2:
        return False
    hits = sum(1 for lab in labels if _TIME_LABEL.match(str(lab).strip()))
    return hits >= max(2, int(len(labels) * 0.6))


def _choose_chart_type(labels: list[str], values: list, _requested=None) -> str:
    """Type comes from the series, not from the model's CHART habit (always bar)."""
    n = len(values)
    nums = []
    for val in values:
        try:
            nums.append(float(val))
        except (TypeError, ValueError):
            nums.append(0.0)
    if _looks_like_time(labels):
        return "line"
    all_nonneg = bool(nums) and all(x >= 0 for x in nums)
    if 2 <= n <= 8 and all_nonneg:
        return "pie"
    return "bar"


def _chart_series(rows: list[dict]) -> tuple[list[str], list]:
    keys = list(rows[0].keys())
    if len(keys) < 2:
        return [], []
    label_key, value_key = keys[0], keys[1]
    for k in keys[1:]:
        if any(isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool)
               for r in rows):
            value_key = k
            break
    return [str(r.get(label_key, "")) for r in rows], [r.get(value_key) for r in rows]


HISTORY_TURNS = 6      # how many earlier messages the model may see
HISTORY_CHARS = 800    # per message, so one long answer cannot crowd out the rest


def _history_messages(history) -> list[dict]:
    """Earlier turns, trimmed, for resolving references like "his" or "there".

    Deliberately narrow: only user and assistant text, most recent few turns,
    each truncated. Tool results are never replayed -- if the model wants a
    figure it must call the tool again, which the grounding check enforces
    because the allow-list only ever contains THIS question's results."""
    if not history:
        return []
    out = []
    for item in list(history)[-HISTORY_TURNS:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        out.append({"role": role, "content": content[:HISTORY_CHARS]})
    return out


def ask(question: str, user: str = "local", allow_sensitive: bool = False,
        verbose: bool = True, on_event=None, history=None) -> dict:
    """Answer one question. `on_event`, if given, receives progress events as
    plain dicts so a UI can show what the agent is doing. It can never affect
    the answer: every call is isolated, and the grounding check below runs
    regardless.

    `history` lets a follow-up such as "and in Alexandria?" make sense. It never
    becomes a source of figures: numbers in earlier messages fail the grounding
    check because the allow-list is built only from tools called for this question."""
    def emit(event: dict) -> None:
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception:
            pass

    session = mcp_client.CallSession(user=user, allow_sensitive=allow_sensitive)
    hub = None
    tools = []
    schema = ""
    try:
        hub = mcp_client.get_client()
        hub.start()
        tools = mcp_client.tool_schemas(hub)
        schema = mcp_client.schema_snapshot(allow_sensitive)
        if schema:
            tools = [t for t in tools
                     if t.get("function", {}).get("name") != "search_objects"]
    except Exception as exc:
        emit({"type": "status", "state": "mcp_unavailable", "message": str(exc)})
    messages = [
        {"role": "system", "content": system_prompt(schema)},
        *_history_messages(history),
        {"role": "user", "content": question},
    ]

    retries = GROUNDING_RETRIES
    answer = ""
    call_log: list[dict] = []

    emit({"type": "status", "state": "thinking"})

    for _ in range(MAX_STEPS):
        response = ollama.chat(model=MODEL, messages=messages, tools=tools,
                               options={"temperature": 0})
        message = response["message"]
        messages.append(message)
        calls = message.get("tool_calls") or []

        if calls:
            for call in calls:
                fn = call["function"]
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                if verbose:
                    print(f"  -> {fn['name']}({json.dumps(args, ensure_ascii=False)})")
                emit({"type": "tool_start", "name": fn["name"], "args": args})
                if hub is not None:
                    result = mcp_client.invoke(
                        hub, fn["name"], args, session.allow_sensitive)
                    result = session.finish(fn["name"], args, result)
                else:
                    result = session.finish(fn["name"], args, {
                        "error": "the database MCP server is not connected",
                    })
                call_log.append({"tool": fn["name"], "args": args, "result": result})
                emit({"type": "tool_end", "name": fn["name"], "args": args,
                      "result": result})
                messages.append({"role": "tool",
                                 "content": json.dumps(result, ensure_ascii=False, default=str)})
            continue

        answer = message.get("content") or ""
        if not answer.strip():
            # No tool calls and no text. Accepting this showed the user a blank
            # reply, so spend a step asking for words instead.
            if verbose:
                print("  !! empty reply -- asking again for text")
            messages.append({"role": "user", "content": (
                "You replied with nothing. Answer in words, using the tool results "
                "above. If a field you need is missing, call the tool that has it, or "
                "say plainly that you could not retrieve it.")})
            continue

        bad = ungrounded_numbers(answer, session.returned_numbers)
        if bad and retries > 0:
            retries -= 1
            if verbose:
                print(f"  !! ungrounded {bad} -- asking model to correct")
            emit({"type": "status", "state": "regrounding", "numbers": bad})
            messages.append({"role": "user", "content": (
                f"Your answer contained {bad}, which no tool returned. Do not invent "
                "values. Call the correct tool for the real data, or say you cannot "
                "retrieve it.")})
            continue
        break
    else:
        # Steps ran out while the model was still calling tools -- usually it kept
        # retrying something that returns nothing. Returning "" here showed the
        # user a blank answer, so ask once more with no tools available.
        emit({"type": "status", "state": "summarising"})
        if verbose:
            print(f"  !! step limit ({MAX_STEPS}) reached -- forcing an answer")
        messages.append({"role": "user", "content": (
            "Stop calling tools. Using only the results you already have, give the "
            "answer, or say plainly that you could not retrieve it.")})
        forced = ollama.chat(model=MODEL, messages=messages,
                             options={"temperature": 0})
        answer = forced["message"].get("content") or ""
        # No retry budget is left, so an invented number cannot be corrected --
        # withhold the text rather than let one through unchecked.
        if not answer.strip() or ungrounded_numbers(answer, session.returned_numbers):
            answer = ("I could not retrieve this from the database. Try naming the "
                      "table or filter you want, or ask a simpler question.")

    chart, text_answer = extract_chart(answer)
    out = {
        "answer": text_answer,
        "chart": chart,
        "chart_data": _chart_data(chart, call_log),
        "tools_used": session.calls,
        "calls": call_log,
        "ungrounded": ungrounded_numbers(answer, session.returned_numbers),
    }
    emit({"type": "done", "payload": out})
    return out


def main() -> None:
    if len(sys.argv) > 1:
        out = ask(" ".join(sys.argv[1:]))
        print("\n" + out["answer"])
        if out.get("chart_data"):
            print(f"\n[chart] {json.dumps(out['chart_data'], ensure_ascii=False)}")
        return

    print(f"\nAgent ready  (model: {MODEL}, db: {catalog.DB_URL})")
    print("Ask a question, or 'quit' to exit.\n")
    while True:
        try:
            q = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        if q.lower() in {"quit", "exit"}:
            break

        out = ask(q)
        print(f"\nbot > {out['answer']}")
        if out.get("chart_data"):
            print(f"      [chart] {json.dumps(out['chart_data'], ensure_ascii=False)}")
        if out["ungrounded"]:
            print(f"      WARNING ungrounded numbers: {out['ungrounded']}")
        print()


if __name__ == "__main__":
    main()
