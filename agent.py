#!/usr/bin/env python3
"""
AGENT — connects Ollama to the database tools and to DBHub.

This is the loop: question -> tool call -> result -> maybe another tool
call -> grounded answer. Curated tools (db_tools) handle the common
shapes; DBHub's execute_sql is the escape hatch for questions those
tools cannot express, still under guard_sql and the grounding check.

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
import db_tools
import mcp_client

MODEL = "gemma4:12b"
MAX_STEPS = 8
GROUNDING_RETRIES = 1

SYSTEM_PROMPT = """You are a data assistant for an Egyptian company.

HARD RULES
1. Never state a number, name, salary or fact that did not appear in a tool
   result. You have no knowledge of this company's data.
2. search_entities returns only ids and names. For salaries, departments or
   any other detail you MUST call get_records with the id.
3. Never guess an id. Always obtain it from search_entities first.
4. If a search returns several matches, ask the user which one they mean.
   Do not choose for them.
5. If you cannot obtain the data, say so plainly. Never estimate or guess.
6. Reply in the language of the user's LATEST message: Arabic if it is in
   Arabic, English if it is in English. These rules are in English; that is not
   a reason to answer in English.
7. Earlier messages show what has already been discussed, so you can tell what
   "he", "there" or "the same period" refers to. Treat them as wording, not as
   data: look every figure up again instead of repeating one, and never reuse an
   id from them. They are correct as far as they go -- do not apologise for them
   or announce corrections to them.
8. Prefer the curated tools (get_metric, search_entities, list_entities,
   get_records, describe_metric, list_data_areas) for standard questions.
   Use search_objects then execute_sql only when those cannot express the
   question -- especially a date that is not last_month, last_quarter, ytd or
   last_year. execute_sql may only SELECT from the curated views.

WHEN YOU ANSWER
- State the figure with its unit, and name the entity it belongs to, so the
  user can see it is the right one.
- If a chart would help, end with one line:
  CHART: {"type":"bar|line|pie","x":"<field>","y":"<field>","title":"<title>"}
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
    """Pull the rows a chart spec refers to from actual tool results, so a
    frontend renders real grounded numbers rather than trusting anything
    the model wrote in the CHART spec itself."""
    if not chart:
        return None
    for entry in reversed(call_log):
        rows = _rows_from_tool_result(entry)
        if not rows:
            continue
        labels, values = _chart_series(rows)
        if not labels:
            continue
        return {
            "type": chart.get("type", "bar"),
            "title": chart.get("title") or entry["result"].get("metric", "") or "",
            "unit": entry["result"].get("unit", ""),
            "labels": labels,
            "values": values,
        }
    return None


def _rows_from_tool_result(entry: dict) -> list[dict]:
    result = entry.get("result") or {}
    if not isinstance(result, dict):
        return []
    if entry.get("tool") == "get_metric" and result.get("results"):
        return [r for r in result["results"] if isinstance(r, dict)]
    sets = result.get("resultSets") or []
    data = result.get("data")
    if isinstance(data, dict):
        sets = list(sets) + list(data.get("statements") or [])
    if sets and isinstance(sets[0], dict) and isinstance(sets[0].get("rows"), list):
        return [r for r in sets[0]["rows"] if isinstance(r, dict)]
    return []


def _chart_series(rows: list[dict]) -> tuple[list[str], list]:
    if all("group_key" in r and "value" in r for r in rows):
        return [str(r.get("group_key", "")) for r in rows], [r.get("value") for r in rows]
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
    becomes a source of figures: ids in it fail the provenance guard because the
    session below is new, and numbers in it fail the grounding check because the
    allow-list is built only from tools called for this question."""
    def emit(event: dict) -> None:
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception:
            pass

    session = db_tools.ToolSession(user=user, allow_sensitive=allow_sensitive)
    curated = db_tools.tool_schemas()
    curated_names = {t["function"]["name"] for t in curated}
    hub = None
    hub_tools = []
    try:
        hub = mcp_client.get_client()
        hub.start()
        hub_tools = mcp_client.tool_schemas(hub)
    except Exception as exc:
        emit({"type": "status", "state": "mcp_unavailable", "message": str(exc)})
    tools = curated + hub_tools
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
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
                if fn["name"] in curated_names:
                    result = session.call(fn["name"], args)
                elif hub is not None:
                    result = mcp_client.invoke(
                        hub, fn["name"], args, session.allow_sensitive)
                    result = session._finish(fn["name"], args, result)
                else:
                    result = session._finish(fn["name"], args, {
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
                      "metric and period you want, or ask about one person at a time.")

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
        if out["chart"]:
            print(f"\n[chart spec] {json.dumps(out['chart'], ensure_ascii=False)}")
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
        if out["chart"]:
            print(f"      [chart] {json.dumps(out['chart'], ensure_ascii=False)}")
        if out["ungrounded"]:
            print(f"      WARNING ungrounded numbers: {out['ungrounded']}")
        print()


if __name__ == "__main__":
    main()
