# Project context

Read this first. It explains WHY the code is built the way it is, not just
what it does. Written for a new session that has no memory of the planning
conversation that produced this project.

## What this is

A locally-hosted agentic text-to-SQL system for a company database. No data
may leave the organization — no cloud APIs, no external model calls. Users
ask questions in Arabic or English; the system queries the database via
DBHub and returns data, insights, and charts drawn from the SQL rows.

Two-person team, two end users, both interns.

## Hard constraints — do not violate these

- **No cloud model calls, ever.** The runtime model is `gemma4:12b` served
  locally by Ollama. `OLLAMA_NO_CLOUD=1` is set for exactly this reason.
  A cloud coding assistant is fine for *writing* the code — it never touches
  company data. It must never be wired into the running system.
- **Only Apache 2.0 / MIT licensed models.** Ruled out: Llama (custom
  license), Gemma 3 and earlier (Gemma Terms of Use, pre-Apache).
- **Read-only database access only.** No INSERT/UPDATE/DELETE/DDL anywhere
  in the tool layer.

## Decisions already made, and why

**Inference engine: Ollama**, not vLLM/SGLang. Rationale: 2 concurrent
users means no meaningful concurrency pressure, so vLLM/SGLang's throughput
advantages don't apply. Ollama isolates the real risk (model accuracy) from
infrastructure risk. Migration to vLLM later is a config change, not a
rewrite, because everything speaks the OpenAI-compatible API — never call
Ollama-specific features from application code.

**Model: `gemma4:12b`**, chosen over `qwen3.6:27b` and `gemma4:26b` by
empirical testing, not published benchmarks. Do not casually upgrade to a
bigger model to fix tool-loop failures: those were schema-discovery problems,
not model-size problems. Only reconsider if a NEW eval task fails.

**No parallel tool layer beside DBHub.** Six curated Python tools
(`list_data_areas`, `search_entities`, `list_entities`, `get_metric`,
`get_records`, `describe_metric`) used to sit in front of the database.
They are gone. Do not resurrect `db_tools.py` or `mcp_server.py`.

## Two real bugs found during testing, and their fixes — do not regress these

**Bug 1: the model fabricated a salary.** A lookup returned a record with
no salary field; the model invented "15,000" in fluent prose instead of
querying the salary column. Fixed two ways:
  - System prompt hard rule: "never state a number that did not appear in
    a tool result" (see `system_prompt()` in `agent.py`).
  - A **grounding check**: every number in the final answer is checked
    against everything tools actually returned (`ungrounded_numbers()` in
    `agent.py`). If ungrounded, the model gets one retry with an explicit
    correction message before the answer ships.
  - This check must survive any refactor. It is the single most important
    piece of defensive code in the project.

**Bug 2: cross-script name search failed.** A user typed "Ahmed Hassan" in
Latin script; the database stored the name in Arabic only, so search
returned nothing. Fixed by storing BOTH scripts in one normalized search
column (`name_norm` = Arabic-folded name + " | " + English name, see
`setup_demo.py` and the README's bilingual-names section). Any new
searchable entity needs this same dual-script column, or the same failure
will recur silently.

**Bug 3: eight `search_objects` calls, no SQL.** After the curated tools
were removed, the prompt said "discover the schema first." The 12B model
guessed table names (`%revenue%`, `%sale%`, …) until `MAX_STEPS` ran out.
Fixed by injecting a live schema snapshot into the system prompt and
hiding `search_objects` when that snapshot succeeds. Do not raise
`MAX_STEPS` to paper over schema hunting.

## Architecture

```
agent.py --calls--> mcp_client.py --> DBHub (stdio, readonly) --> database
```

Python calls `schema_snapshot()` once (cached) and puts table/column names
in the prompt. The model then calls `execute_sql`. `search_objects` remains
on DBHub but is not offered to the model unless the snapshot fails.

`mcp_client.guard_sql()` runs before SQL reaches DBHub: one SELECT, optional
object allow-list, sensitive identifiers blocked unless `allow_sensitive`.
DBHub's `readonly = true` only stops writes. The grounding check in
`agent.py` still applies to every number.

Charts are built from the SQL rows, not from the model's `CHART:` line.
A single total is a KPI; two or more groups become pie (categories), line
(time), or bar (the rest).

`catalog.py` is the file to change per deployment — `DB_URL`, optional
`SENSITIVE_IDENTIFIERS`, optional `ALLOWED_OBJECTS`. Currently defaults to
`demo.db`. **The real database connection string should never be pasted into
a cloud AI tool.**

## Tool design

`execute_sql` is weaker than the old parameterized tools (the model authors
SQL). The remaining guarantees are the SELECT-only guard, the optional
allow-list, the sensitive-name gate, the row cap, the audit log, and
grounding.

## Known gaps / next steps

1. Eval set currently tests tool CHOICE, not answer CORRECTNESS. Need
   30–50 real company questions with verified correct answers written
   down (`evals/` is not in this repo; build it fresh).
2. No gateway yet (LiteLLM recommended) — needed for per-user auth and a
   second audit trail before this touches real salary data.
3. DBHub is launched by `mcp_client.py` over stdio using the official
   `mcp` Python SDK. Do not resurrect `mcp_server.py`.

## If you are asked to add a feature

Preserve, in order of importance:
1. The grounding check — never let a code path skip it
2. Read-only SQL — `guard_sql` must stay in front of execute_sql
3. Do not reintroduce a parallel tool layer beside DBHub
4. `catalog.py` stays connection + policy, not a schema dump
5. Schema snapshot stays in the prompt — do not send the model back to
   guessing table names with `search_objects`
