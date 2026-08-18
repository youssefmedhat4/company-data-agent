#!/usr/bin/env python3
"""
WEBAPP -- a minimal local chat UI in front of agent.py.

Runs a Flask server bound to 127.0.0.1 only (not reachable from the
network). The browser talks to this process over loopback HTTP; this
process talks to Ollama and the database exactly the way agent.py's CLI
does. Nothing here bypasses mcp_client.guard_sql() -- this file
adds zero new logic beyond serving the page and forwarding the question
to agent.ask().

Run:
    python webapp.py
Then open http://127.0.0.1:5000
"""

from __future__ import annotations

import json
import os
import queue
import socket
import threading

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

import agent
import mcp_client

app = Flask(__name__, static_folder="static", static_url_path="")


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/health")
def api_health():
    """Lets the UI show which model and database are actually in use. `pid`
    is here because a server left running from an earlier session keeps
    answering with the process that loaded first."""
    return jsonify({
        "model": agent.MODEL,
        "db": agent.catalog.DB_URL,
        "pid": os.getpid(),
        "mcp": _mcp_status(),
    })


def _mcp_status() -> dict:
    """What /api/health reports about DBHub. Starting it here means a stale
    server is visible as mcp.error rather than as the model 'refusing' SQL."""
    try:
        hub = mcp_client.get_client()
        hub.start()
        return {
            "server": "dbhub",
            "how": hub.description,
            "tools": sorted(hub.tool_names()),
        }
    except Exception as exc:
        return {"server": "dbhub", "error": str(exc)}


@app.get("/api/schema")
def api_schema():
    """Live table/view names for the empty-state prompts. Never returns rows."""
    try:
        tables = mcp_client.visible_tables(allow_sensitive=False)
    except Exception as exc:
        return jsonify({"tables": [], "error": str(exc)})
    prompts = [{"text": "What tables and views are in this database?",
                "hint": "lists tables and views"}]
    if tables:
        sample = tables[0]
        prompts.append({"text": f"Show 5 rows from {sample}",
                        "hint": "sample of live data"})
        prompts.append({"text": f"How many rows are in {sample}?",
                        "hint": "a count from the database"})
        if len(tables) > 1:
            prompts.append({"text": f"What columns does {tables[1]} have?",
                            "hint": "schema detail"})
    else:
        prompts.append({"text": "What data can I ask about?",
                        "hint": "lists what is readable"})
    return jsonify({"tables": tables, "prompts": prompts[:4]})


def _read_question():
    body = request.get_json(silent=True) or {}
    history = body.get("history")
    return ((body.get("question") or "").strip(),
            bool(body.get("allow_sensitive")),
            history if isinstance(history, list) else None)


@app.post("/api/ask")
def api_ask():
    question, allow_sensitive, history = _read_question()
    if not question:
        return jsonify({"error": "empty question"}), 400

    out = agent.ask(question, allow_sensitive=allow_sensitive, verbose=False,
                    history=history)
    return jsonify(out)


@app.post("/api/ask/stream")
def api_ask_stream():
    """Same answer as /api/ask, but progress events arrive as they happen so the
    UI can show which tool is running instead of a blank wait. The answer itself
    is still produced by the one agent.ask() call below, grounding check
    included -- these events are reporting only."""
    question, allow_sensitive, history = _read_question()
    if not question:
        return jsonify({"error": "empty question"}), 400

    events: queue.Queue = queue.Queue()

    def run():
        try:
            agent.ask(question, allow_sensitive=allow_sensitive, verbose=False,
                      on_event=events.put, history=history)
        except Exception as exc:
            events.put({"type": "error", "message": str(exc)})
        finally:
            events.put(None)

    threading.Thread(target=run, daemon=True).start()

    @stream_with_context
    def generate():
        yield ": connected\n\n"
        while True:
            event = events.get()
            if event is None:
                break
            yield "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _port_already_serving(port: int = 5000) -> bool:
    """Windows lets a second process bind a loopback port that is already in use,
    after which either server may answer. The older one keeps serving the code
    it loaded at startup, so edits appear to have no effect. Refuse instead."""
    with socket.socket() as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    if _port_already_serving():
        raise SystemExit(
            "A server is already answering on http://127.0.0.1:5000.\n"
            "Stop it first, or this one will compete with it and you may keep "
            "getting answers from the older code:\n"
            '  PowerShell:  Get-CimInstance Win32_Process -Filter "Name=\'python.exe\'" |\n'
            '                 Where-Object CommandLine -like "*webapp.py*" |\n'
            "                 ForEach-Object { Stop-Process -Id $_.ProcessId }"
        )
    print(f"Web UI ready: http://127.0.0.1:5000  (model: {agent.MODEL}, db: {agent.catalog.DB_URL})")
    try:
        mcp_client.get_client().start()
        print(f"DBHub ready: {mcp_client.get_client().description}")
    except Exception as exc:
        print(f"DBHub NOT ready: {exc}")
    app.run(host="127.0.0.1", port=5000, threaded=True)
