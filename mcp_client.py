#!/usr/bin/env python3
"""
MCP CLIENT -- connects this project to DBHub, a real MCP server.

DBHub (https://github.com/bytebase/dbhub) is a universal database MCP server
from Bytebase. It speaks the database; we speak MCP to it. This module:

  1. finds a Node runtime new enough to run DBHub (>= 22.5.0, because DBHub
     opens SQLite through the built-in `node:sqlite` module),
  2. launches DBHub as a child process over stdio, configured read-only by
     dbhub.toml,
  3. discovers its tools at runtime and converts their MCP input schemas into
     the Ollama/OpenAI tool-schema format agent.py already uses,
  4. guards every SQL statement before it reaches the database.

WHY A GUARD IF DBHUB IS ALREADY READ-ONLY: DBHub's `readonly = true` stops
writes -- it does not stop a SELECT from reading a salary. This project's
privacy toggle is about WHICH ROWS AND COLUMNS may be read, which is a policy
only this project knows. So guard_sql() below is not duplicated effort; it
enforces a different rule than DBHub does. The two layers:

    DBHub  : no writes at all (keyword classifier + PRAGMA query_only = ON)
    here   : one read-only SELECT, optional ALLOWED_OBJECTS, and nothing
             matching SENSITIVE_IDENTIFIERS unless allow_sensitive is True

THREADING: the MCP Python SDK is async and agent.py is synchronous, so the
session lives in one dedicated background thread with its own event loop and
is reached through asyncio.run_coroutine_threadsafe. Calls are serialised by
a lock, because one MCP session is a single request/response channel.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import catalog

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = PROJECT_DIR / "dbhub.toml"
SERVER_LOG = PROJECT_DIR / "dbhub.log"

# DBHub needs node:sqlite, which landed in Node 22.5.0.
MIN_NODE = (22, 5, 0)

START_TIMEOUT_SEC = 90     # first run may have to unpack DBHub
CALL_TIMEOUT_SEC = 30


# ===================================================================
# Locating a Node runtime that can actually run DBHub
# ===================================================================

def _node_version(exe: str) -> tuple[int, int, int] | None:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", out)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def find_runtime() -> tuple[list[str], str]:
    """Return (argv_prefix, description) for launching DBHub.

    Preference order, and why:
      1. the Node pinned in this project's node_modules -- no network, no
         surprise version bumps, and known to satisfy MIN_NODE;
      2. any Node on PATH that is new enough;
      3. npx, which downloads DBHub on demand. Last resort: it needs network
         access at question time, which a locked-down deployment will not have.
    """
    local_node = PROJECT_DIR / "node_modules" / "node" / "bin" / (
        "node.exe" if os.name == "nt" else "node")
    local_dbhub = PROJECT_DIR / "node_modules" / "@bytebase" / "dbhub" / "dist" / "index.js"

    if local_dbhub.exists():
        for exe in (str(local_node), shutil.which("node")):
            if not exe:
                continue
            ver = _node_version(exe)
            if ver and ver >= MIN_NODE:
                return ([exe, str(local_dbhub)],
                        f"local DBHub on Node {'.'.join(map(str, ver))} ({exe})")

    npx = shutil.which("npx")
    if npx:
        return ([npx, "-y", "@bytebase/dbhub@1.2.0"], f"npx ({npx})")

    raise RuntimeError(
        "No way to launch DBHub. Install the pinned copy with `npm install` in "
        f"{PROJECT_DIR}, which also provides a Node >= "
        f"{'.'.join(map(str, MIN_NODE))} runtime."
    )


def sqlite_dsn() -> str:
    """Turn catalog.DB_URL into a SQLite DSN DBHub can actually open.

    `sqlite:///./demo.db` parses as the path `/./demo.db`, which is not the
    file in this folder. DBHub's parser treats `sqlite:///C:/...` as a Windows
    absolute path, so we resolve against PROJECT_DIR first.
    """
    raw = catalog.DB_URL
    if not raw.startswith("sqlite:"):
        return raw
    path = raw.split("sqlite:", 1)[1].lstrip("/")
    if path.startswith("./"):
        path = path[2:]
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_DIR / p
    p = p.resolve()
    if not p.exists():
        raise RuntimeError(f"SQLite database not found: {p}")
    return "sqlite:///" + p.as_posix()


def _runtime_config() -> Path:
    """dbhub.toml with the DSN rewritten to an absolute path DBHub can open."""
    text = CONFIG_FILE.read_text(encoding="utf-8")
    text = re.sub(r'^dsn\s*=\s*".*"', f'dsn = "{sqlite_dsn()}"', text,
                  count=1, flags=re.M)
    out = PROJECT_DIR / "dbhub.runtime.toml"
    out.write_text(text, encoding="utf-8")
    return out


def server_command() -> tuple[list[str], str]:
    argv, how = find_runtime()
    return argv + ["--config", str(_runtime_config()), "--transport", "stdio"], how


# ===================================================================
# The SQL guard
# ===================================================================
# Objects the model may FROM/JOIN. Empty ALLOWED_OBJECTS means generic mode:
# any table or view is fine (minus sqlite internals and sensitive names).

def _allowed_objects() -> set[str] | None:
    allowed = {n.lower() for n in getattr(catalog, "ALLOWED_OBJECTS", ()) if n}
    return allowed or None


def _sensitive_tokens() -> set[str]:
    return {t.lower() for t in getattr(catalog, "SENSITIVE_IDENTIFIERS", ()) if t}


# Anything that writes, changes session state, reaches the filesystem, or opens
# a second database. DBHub's own read-only mode permits PRAGMA and EXPLAIN; this
# project does not. Schema comes from schema_snapshot(), not from PRAGMA.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|replace|truncate|rename"
    r"|attach|detach|pragma|vacuum|reindex|analyze|explain|begin|commit"
    r"|rollback|savepoint|release|grant|revoke|set|call|do|merge|upsert"
    r"|load_extension|readfile|writefile|edit|fts3_tokenizer)\b",
    re.IGNORECASE,
)

# SQLite's own catalogue. Reading it would disclose the full schema, including
# the DDL text of restricted views.
_INTERNAL = re.compile(r"\bsqlite_(master|schema|temp_master|temp_schema|sequence|stat\d*)\b",
                       re.IGNORECASE)

_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_OBJECT_REF = re.compile(
    r"\b(?:from|join)\s+(\"[^\"]+\"|\[[^\]]+\]|`[^`]+`|[A-Za-z_][A-Za-z0-9_.$]*)",
    re.IGNORECASE)
_CTE_NAME = re.compile(r"(?:\bwith\b\s+(?:recursive\s+)?|,)\s*([A-Za-z_]\w*)\s+as\s*\(",
                       re.IGNORECASE)


def guard_sql(sql: str, allow_sensitive: bool = False) -> str | None:
    """Return an error message if this SQL must not run, or None to allow it.

    Deliberately conservative: when in doubt it refuses, because a false
    refusal costs the model one retry while a false approval costs a data leak.
    The message is written for the model, so it can fix the query and try again
    rather than giving up.
    """
    if not isinstance(sql, str) or not sql.strip():
        return "sql is required and must be a non-empty string."

    body = _COMMENT.sub(" ", sql)

    # One statement. A trailing semicolon is fine; a second statement is not,
    # because the guard would then be reasoning about only part of what runs.
    if ";" in body.strip().rstrip(";"):
        return ("only one statement per call. Remove the extra ';' and send a "
                "single SELECT.")

    stripped = body.strip().rstrip(";").strip()
    if not re.match(r"^(select|with)\b", stripped, re.IGNORECASE):
        return ("only a single read-only SELECT is allowed (a WITH ... SELECT is "
                "fine). This tool cannot modify data or read schema metadata.")

    bad = _FORBIDDEN.search(stripped)
    if bad:
        return (f"'{bad.group(1)}' is not permitted. This tool runs one read-only "
                "SELECT; it cannot write, change settings, or run PRAGMA/EXPLAIN.")

    if _INTERNAL.search(stripped):
        return ("SQLite's internal tables are not readable. Use the SCHEMA in "
                "the system prompt, or search_objects, to find names.")

    # Object allow-list. None means generic mode: any table/view is fine.
    # Names introduced by a WITH clause are allowed because they resolve to a
    # SELECT that is itself checked by this same pass.
    allowed = _allowed_objects()
    ctes = {c.lower() for c in _CTE_NAME.findall(stripped)}
    if allowed is not None:
        for raw in _OBJECT_REF.findall(stripped):
            obj = raw.strip('"[]`').lower()
            obj = obj.rsplit(".", 1)[-1]
            if obj in ctes or obj in allowed:
                continue
            return (f"'{obj}' cannot be read. Readable objects are: "
                    f"{sorted(allowed)}. Query one of those instead.")

    if not allow_sensitive:
        for token in _sensitive_tokens():
            if re.search(rf"\b{re.escape(token)}\b", stripped, re.IGNORECASE):
                return ("this query touches restricted data, which the current "
                        "user is not permitted to see. Answer without it, or ask "
                        "the user to allow sensitive data.")

    return None


def _audit(tool: str, args: dict, rows: int, user: str, allow_sensitive: bool) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user": user,
        "tool": tool,
        "args": args,
        "rows": rows,
        "allow_sensitive": allow_sensitive,
    }
    try:
        with open(catalog.AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


class CallSession:
    """One question: numbers returned (for grounding) and the audit trail."""

    def __init__(self, user: str = "local", allow_sensitive: bool = False):
        self.user = user
        self.allow_sensitive = allow_sensitive
        self.returned_numbers: set[int] = set()
        self.calls: list[str] = []

    def remember(self, payload) -> None:
        if isinstance(payload, dict):
            for v in payload.values():
                self.remember(v)
        elif isinstance(payload, list):
            for v in payload:
                self.remember(v)
        elif isinstance(payload, (int, float)) and not isinstance(payload, bool):
            self.returned_numbers.add(int(payload))

    def finish(self, tool: str, args: dict, payload):
        self.calls.append(tool)
        self.remember(payload)
        n = len(payload) if isinstance(payload, list) else 1
        _audit(tool, args, n, self.user, self.allow_sensitive)
        return payload


# ===================================================================
# The client
# ===================================================================

class DBHubError(RuntimeError):
    pass


class DBHubClient:
    """One long-lived DBHub process, shared by every question.

    Starting DBHub costs a second or two, so it is started once and kept. It
    holds no per-user state -- the privacy decision is made here, per call, by
    guard_sql -- so sharing it between questions is safe.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: ClientSession | None = None
        self._stop: asyncio.Event | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._tools: list = []
        self._how = ""
        self._lock = threading.Lock()      # one request at a time on one channel
        self._start_lock = threading.Lock()

    # -- lifecycle --------------------------------------------------
    def start(self, timeout: float = START_TIMEOUT_SEC) -> None:
        with self._start_lock:
            if self._ready.is_set() and self._error is None:
                return
            if catalog.MAX_ROWS != _toml_max_rows():
                raise DBHubError(
                    f"dbhub.toml max_rows ({_toml_max_rows()}) does not match "
                    f"catalog.MAX_ROWS ({catalog.MAX_ROWS}). Make them equal so the "
                    "row cap cannot drift.")
            argv, self._how = server_command()
            self._ready.clear()
            self._error = None
            self._thread = threading.Thread(target=self._run, args=(argv,),
                                            name="dbhub", daemon=True)
            self._thread.start()
            if not self._ready.wait(timeout):
                raise DBHubError(
                    f"DBHub did not start within {timeout}s. Command: {' '.join(argv)}. "
                    f"See {SERVER_LOG.name} for its output.")
            if self._error is not None:
                raise DBHubError(
                    f"DBHub failed to start ({self._error}). Command: "
                    f"{' '.join(argv)}. See {SERVER_LOG.name}.")

    def _run(self, argv: list[str]) -> None:
        try:
            asyncio.run(self._serve(argv))
        except BaseException as exc:            # noqa: BLE001 -- reported to start()
            self._error = exc
            self._ready.set()

    async def _serve(self, argv: list[str]) -> None:
        params = StdioServerParameters(
            command=argv[0], args=argv[1:], cwd=str(PROJECT_DIR),
            encoding="utf-8", encoding_error_handler="replace")
        # DBHub logs to stderr. Keep it out of the console but on disk, or a
        # connection failure looks like a silent hang.
        with open(SERVER_LOG, "a", encoding="utf-8", errors="replace") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._loop = asyncio.get_running_loop()
                    self._stop = asyncio.Event()
                    self._tools = list((await session.list_tools()).tools)
                    self._ready.set()
                    await self._stop.wait()

    def stop(self) -> None:
        if self._loop and self._stop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(timeout=10)
        self._ready.clear()
        self._session = None
        self._loop = None

    @property
    def description(self) -> str:
        return self._how

    # -- MCP surface ------------------------------------------------
    def tools(self) -> list:
        self.start()
        return list(self._tools)

    def tool_names(self) -> set[str]:
        return {t.name for t in self.tools()}

    def call(self, name: str, args: dict, timeout: float = CALL_TIMEOUT_SEC) -> dict:
        """Call one DBHub tool. Never raises: a failure comes back as
        {"error": ...} so agent.py's loop can show the model what went wrong
        and let it try something else.
        """
        try:
            self.start()
        except Exception as exc:                # noqa: BLE001
            return {"error": f"database MCP server unavailable: {exc}"}

        async def run():
            return await asyncio.wait_for(
                self._session.call_tool(name, args,
                                        read_timeout_seconds=timedelta(seconds=timeout)),
                timeout + 5)

        try:
            with self._lock:
                if self._session is None or self._loop is None:
                    return {"error": "database MCP server is not connected"}
                future = asyncio.run_coroutine_threadsafe(run(), self._loop)
                result = future.result(timeout + 10)
        except (asyncio.TimeoutError, TimeoutError):
            return {"error": f"the query took longer than {timeout}s and was cancelled. "
                             "Ask for less data, or narrow the date range."}
        except Exception as exc:                # noqa: BLE001
            return {"error": f"database MCP server call failed: {exc}"}
        return _unwrap(result)


def invoke(client: "DBHubClient", name: str, args: dict,
           allow_sensitive: bool = False) -> dict:
    """Run one DBHub tool under this project's rules, then return a dict the
    model can read. Never raises.

    execute_sql is intercepted: a query that fails guard_sql never reaches
    DBHub. search_objects is filtered afterwards so a restricted view cannot
    leak its column list when the toggle is off.
    """
    args = dict(args or {})
    if name == "execute_sql":
        err = guard_sql(args.get("sql") or "", allow_sensitive)
        if err:
            return {"error": err}

    if name not in client.tool_names():
        return {"error": f"unknown MCP tool {name}. Valid: {sorted(client.tool_names())}"}

    result = client.call(name, args)
    if name == "search_objects" and not allow_sensitive:
        result = _redact_sensitive(result)
    return _cap_rows(result)


def _redact_sensitive(payload):
    """Drop schema entries whose name is a restricted view, column, or table."""
    blocked = _sensitive_tokens()

    def name_of(item):
        if isinstance(item, str):
            return item.lower()
        if isinstance(item, dict):
            for key in ("name", "object", "table", "view"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    return value.lower()
        return ""

    def walk(node):
        if isinstance(node, list):
            return [walk(v) for v in node if name_of(v) not in blocked]
        if isinstance(node, dict):
            if name_of(node) in blocked:
                return {}
            return {k: walk(v) for k, v in node.items()}
        return node

    return walk(payload)


def _cap_rows(payload):
    """Second row cap, in case DBHub's max_rows and catalog.MAX_ROWS ever drift
    after startup (startup already asserts they match)."""
    if not isinstance(payload, dict):
        return payload
    cap = catalog.MAX_ROWS
    buckets = list(payload.get("resultSets") or [])
    data = payload.get("data")
    if isinstance(data, dict):
        buckets.extend(data.get("statements") or [])
    for set_ in buckets:
        if not isinstance(set_, dict):
            continue
        rows = set_.get("rows")
        if isinstance(rows, list) and len(rows) > cap:
            set_["rows"] = rows[:cap]
            set_["rowCount"] = cap
            set_["count"] = cap
            set_["truncated"] = True
    return payload


def _toml_max_rows() -> int | None:
    """Read max_rows straight out of dbhub.toml, so the mismatch check compares
    what DBHub will actually enforce rather than what we hope it enforces."""
    try:
        text = CONFIG_FILE.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^\s*max_rows\s*=\s*(\d+)", text, re.M)
    return int(m[1]) if m else None


def _unwrap(result) -> dict:
    """Turn an MCP CallToolResult into the plain dict agent.py feeds the model.

    DBHub answers with a JSON document inside a text content block. Parse it
    when we can, keep the text when we cannot, and never lose the isError flag.
    """
    chunks = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text is not None:
            chunks.append(text)
    payload = "\n".join(chunks).strip()

    parsed: object
    if payload:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = payload
    else:
        parsed = getattr(result, "structuredContent", None) or {}

    if getattr(result, "isError", False):
        return {"error": parsed if isinstance(parsed, str) else json.dumps(
            parsed, ensure_ascii=False, default=str)}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


# ===================================================================
# Tool schemas for Ollama
# ===================================================================
# Extra wording appended to DBHub's own descriptions. DBHub describes what its
# tools DO; these lines tell the model when to reach for them in THIS system.

_GUIDANCE = {
    "execute_sql": (
        "\n\nWrite ONE read-only SELECT (a WITH ... SELECT is fine) against "
        "the SCHEMA in the system prompt. Semicolon-separated statements, "
        "writes, DDL, PRAGMA and EXPLAIN are refused. Alias every aggregate, "
        "e.g. SUM(amount) AS total. Do not invent table or column names."
    ),
    "search_objects": (
        "\n\nOnly if SCHEMA is missing a name you need. It returns names and "
        "types, never row data. Do not hunt for a table named after the metric "
        "(there is no table called revenue). Restricted objects are omitted "
        "when the current user may not see them."
    ),
}

# Keys some MCP servers emit that Ollama's tool schema does not expect.
_SCHEMA_DROP = {"$schema", "additionalProperties", "definitions", "$defs", "title"}


def _clean_schema(schema: dict | None) -> dict:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    out = {k: v for k, v in schema.items() if k not in _SCHEMA_DROP}
    out.setdefault("type", "object")
    out.setdefault("properties", {})
    return out


def visible_tables(allow_sensitive: bool = False) -> list[str]:
    """Table/view names for the empty-state prompts. Names only, never rows."""
    names: list[str] = []
    seen: set[str] = set()
    for line in schema_snapshot(allow_sensitive).splitlines():
        m = re.match(r"(?:table|view)\s+([A-Za-z_][\w.$]*)", line, re.I)
        if not m:
            continue
        name = m.group(1)
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names[:20]


_snapshot_lock = threading.Lock()
_snapshot_cache: dict[bool, str] = {}


def schema_snapshot(allow_sensitive: bool = False) -> str:
    """Compact live schema for the system prompt: `table name (col TYPE, ...)`.

    Fetched here so the model does not spend its step budget guessing table
    names with search_objects. Names only — no row counts, no sample rows.
    """
    with _snapshot_lock:
        cached = _snapshot_cache.get(allow_sensitive)
        if cached:
            return cached
    lines: list[str] = []
    try:
        client = get_client()
        for object_type in ("table", "view"):
            result = invoke(client, "search_objects", {
                "object_type": object_type,
                "detail_level": "full",
                "limit": 40,
            }, allow_sensitive)
            data = result.get("data") if isinstance(result, dict) else None
            rows = (data or {}).get("results") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                continue
            blocked = set() if allow_sensitive else _sensitive_tokens()
            for obj in rows:
                if not isinstance(obj, dict):
                    continue
                name = obj.get("name")
                if not isinstance(name, str) or name.lower() in blocked:
                    continue
                cols = []
                for col in obj.get("columns") or []:
                    if not isinstance(col, dict):
                        continue
                    cname = col.get("name")
                    if not isinstance(cname, str) or cname.lower() in blocked:
                        continue
                    ctype = str(col.get("type") or "").strip()
                    cols.append(f"{cname} {ctype}".strip() if ctype else cname)
                    if len(cols) >= 30:
                        break
                coltxt = ", ".join(cols)
                if len(obj.get("columns") or []) > 30:
                    coltxt += ", …"
                lines.append(f"{object_type} {name} ({coltxt})" if coltxt
                             else f"{object_type} {name}")
    except Exception:
        return ""
    text = "\n".join(lines)
    if text:
        with _snapshot_lock:
            _snapshot_cache[allow_sensitive] = text
    return text


def tool_schemas(client: "DBHubClient") -> list[dict]:
    """DBHub's live tool list, in Ollama/OpenAI function-calling format.

    The schemas are DBHub's own, fetched over MCP at startup rather than copied
    from its docs, so a DBHub upgrade that adds an argument is picked up without
    editing this file.
    """
    schemas = []
    for tool in client.tools():
        description = (tool.description or "").strip() + _GUIDANCE.get(tool.name, "")
        schemas.append({"type": "function", "function": {
            "name": tool.name,
            "description": description,
            "parameters": _clean_schema(getattr(tool, "inputSchema", None)),
        }})
    return schemas


# ===================================================================
# Module-level singleton
# ===================================================================

_client: DBHubClient | None = None
_client_lock = threading.Lock()


def get_client() -> DBHubClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = DBHubClient()
    return _client


def shutdown() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.stop()
            _client = None


def main() -> None:
    """Smoke test: start DBHub, list its tools, run one guarded query."""
    client = get_client()
    client.start()
    print(f"DBHub launched via {client.description}")
    print(f"config: {CONFIG_FILE}")
    for tool in client.tools():
        params = sorted(_clean_schema(tool.inputSchema).get("properties", {}))
        print(f"  tool  {tool.name}({', '.join(params)})")

    sql = "SELECT COUNT(*) AS n FROM v_orders"
    print(f"\nguard_sql -> {guard_sql(sql)}")
    print(f"query     -> {json.dumps(client.call('execute_sql', {'sql': sql}), ensure_ascii=False)[:400]}")
    snap = schema_snapshot()
    print(f"\nschema ({len(snap.splitlines())} objects)\n{snap}")

    write = "DELETE FROM v_orders"
    print(f"\nwrite blocked -> {guard_sql(write)}")
    shutdown()


if __name__ == "__main__":
    main()
