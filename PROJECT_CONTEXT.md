# Project context

Read this first. It explains WHY the code is built the way it is, not just
what it does. Written for a new Claude Code session that has no memory of
the planning conversation that produced this project.

## What this is

A locally-hosted agentic text-to-SQL system for a company database. No data
may leave the organization — no cloud APIs, no external model calls. Users
ask questions in Arabic or English; the system queries the database via
tools and returns data, insights, and chart recommendations.

Two-person team, two end users, both interns.

## Hard constraints — do not violate these

- **No cloud model calls, ever.** The runtime model is `gemma4:12b` served
  locally by Ollama. `OLLAMA_NO_CLOUD=1` is set for exactly this reason.
  Claude Code itself is fine for *writing* the code — it never touches
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
*empirical testing*, not published benchmarks. Two custom eval scripts were
run (see `evals/` if present, or recreate from this doc):

1. Single tool-call test, 2 tools, 9 questions (EN + AR) → 100% correct
   tool choice.
2. Multi-step agent loop test, 5 tools, 7 tasks including cross-script
   Arabic/Latin name matching and salary lookups → 100% after fixes below.

**Do not casually upgrade to a bigger model.** The 12B model passed the
loop test cleanly. A bigger model would cost VRAM and speed for no
measurable gain on this task. Only reconsider if a NEW eval task fails.

## Two real bugs found during testing, and their fixes — do not regress these

**Bug 1: the model fabricated a salary.** `search_entities` returned a
record with no salary field; the model invented "15,000" in fluent prose
instead of calling `get_records`. Fixed two ways:
  - System prompt hard rule: "never state a number that did not appear in
    a tool result" (see `agent.py` SYSTEM_PROMPT).
  - A **grounding check**: every number in the final answer is checked
    against everything tools actually returned (`ungrounded_numbers()` in
    `agent.py`). If ungrounded, the model gets one retry with an explicit
    correction message before the answer ships.
  - This check must survive any refactor. It is the single most important
    piece of defensive code in the project.

**Bug 2: cross-script name search failed.** A user typed "Ahmed Hassan" in
Latin script; the database stored the name in Arabic only, so search
returned nothing and the model correctly (but unhelpfully) said "not
found." Fixed by storing BOTH scripts in one normalized search column
(`name_norm` = Arabic-folded name + " | " + English name, see
`setup_demo.py` and the README's "add normalized search columns" section).
Any new searchable entity needs this same dual-script column, or the same
failure will recur silently.

## Architecture

```
agent.py --calls--> db_tools.py  (curated tools)
                 --> mcp_client.py --> DBHub (stdio, readonly) --> views
```

`mcp_client.guard_sql()` is a second gate in front of DBHub's `execute_sql`:
one SELECT, catalog views only, salary blocked unless `allow_sensitive`.
DBHub's own `readonly = true` only stops writes. The grounding check in
`agent.py` still applies to every number, including SQL results.

`db_tools.py` has no MCP and no Ollama dependency by design — it's testable
in isolation. All curated-tool security lives there:
- SELECT-only, parameterized queries, no string-built SQL
- Hard row limit (`catalog.MAX_ROWS`, currently 50)
- ID provenance: `get_records` rejects any id `search_entities` didn't
  issue this session (prevents the "wrong person's salary" failure class)
- Sensitive entities (e.g. `employee_salary`) require `allow_sensitive=True`
- Bidi/control characters stripped from all returned text
- Every call written to `audit.log`

`catalog.py` is the ONLY file meant to change per deployment — connection
string, entities, metrics, periods. Currently points at `demo.db` (SQLite,
fake data) built by `setup_demo.py`. **The real database connection string
and schema details should never be pasted into a cloud AI tool** — edit
`catalog.py` locally by hand, or only with a fully local coding assistant.

## Tool design — curated first, SQL as a guarded escape hatch

Free-form SQL used to be rejected outright. It is now available through
DBHub's `execute_sql`, but only after `guard_sql()`: one SELECT, curated
views only, salary gated. The six curated tools
(`list_data_areas`, `search_entities`, `list_entities`, `get_metric`,
`get_records`, `describe_metric`) remain the default path. Keep total
tool count small — smaller models degrade past ~8, per the loop test
above. `execute_sql` weakens ID provenance for whatever the model
SELECTs; the grounding check and salary gate are what remain.

## Known gaps / next steps

1. Eval set currently tests tool CHOICE, not answer CORRECTNESS. Need
   30–50 real company questions with verified correct answers written
   down, or a system can score 100% on tool choice while returning wrong
   answers (this already happened once with the department/region mixup —
   see chat history if available, or just build the eval set fresh).
2. Chart rendering: `agent.py` emits a `CHART: {...}` JSON spec in the
   answer text. This must be rendered by a frontend, never executed as
   code (matplotlib/exec() etc. is a remote-code-execution risk).
3. No gateway yet (LiteLLM recommended) — needed for per-user auth and a
   second audit trail before this touches real salary data.
4. DBHub is launched by `mcp_client.py` over stdio using the official
   `mcp` Python SDK. Do not resurrect `mcp_server.py`.

## If you (Claude Code) are asked to add a feature

Preserve, in order of importance:
1. The grounding check — never let a code path skip it
2. ID provenance checks — never let a tool accept a client-supplied ID
   without checking it was issued this session
3. Read-only, parameterized queries — never string-concatenate into SQL
4. The catalog.py / db_tools.py separation — don't hardcode schema details
   into db_tools.py
