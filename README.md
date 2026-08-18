# Company Data Agent

Ask questions in Arabic or English, get answers from the connected database.
Everything runs locally. No data leaves the machine.

---

## How the pieces connect

```
  you type a question
         │
         ▼
   webapp.py / agent.py ──► Ollama (gemma4:12b)
         │  ▲
         │  └── execute_sql
         ▼
   mcp_client.py  ►  DBHub (read-only)  ►  your database
         │
    schema_snapshot()   table and column names, injected into the prompt
    guard_sql()         one SELECT, optional allow-list, sensitive-name gate
```

Python loads the live schema once and puts it in the prompt. The model’s job
is `execute_sql`, not hunting for table names. `search_objects` is only
offered if that snapshot fails. Charts are drawn from the SQL rows (pie for
a category split, line for time, bar otherwise). The grounding check still
requires every number in the answer to have come from a tool result.

| File | What it is | Do you edit it? |
|---|---|---|
| `catalog.py` | Connection, sensitive names, row cap | **Yes** |
| `dbhub.toml` | DBHub readonly + row cap | When pointing at a real DB |
| `mcp_client.py` | DBHub launcher + SQL guard + schema snapshot | Rarely |
| `agent.py` | The Ollama loop + grounding check | Rarely |
| `webapp.py` + `static/` | Local chat UI | Rarely |
| `setup_demo.py` | Builds a sample SQLite file | No |

---

## Run it today (demo data)

```powershell
python -m pip install -r requirements.txt
npm install
python setup_demo.py
python webapp.py
```

Then open http://127.0.0.1:5000. `npm install` pulls the pinned DBHub MCP
server. The UI binds to localhost only. The lock button allows identifiers
listed in `SENSITIVE_IDENTIFIERS`; leave it off by default.

CLI instead of the browser:

```powershell
python agent.py
```

Then try:

```
what was revenue last quarter?
كم كانت الإيرادات الربع الماضي؟
what is Sara Ibrahim's salary?
show me revenue by region for the year
which region is شركة النيل in?
```

Watch the `->` lines: those are the SQL calls. A second `python webapp.py`
on the same port is refused; stop the old process first.

---

## Point it at your real database

The agent is generic. It does not need a catalog of your tables. Change the
connection, grant a read-only user, restart.

### Step 1 — set the connection

In `catalog.py` (or the `DB_URL` environment variable):

```
DB_URL = "postgresql+psycopg://agent_ro:pass@host:5432/yourdb"
```

Optional:

```
SENSITIVE_IDENTIFIERS=salary,ssn,password
ALLOWED_OBJECTS=               # empty = every table/view
```

DBHub is pointed at the same database at startup (`dbhub.toml` `dsn` is
rewritten from `DB_URL`). Keep `readonly = true`.

### Step 2 — make a read-only user

**PostgreSQL**
```sql
CREATE USER agent_ro WITH PASSWORD 'change-me';
GRANT CONNECT ON DATABASE yourdb TO agent_ro;
GRANT USAGE ON SCHEMA public TO agent_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agent_ro;
```

Prefer granting only the tables and views the agent should see. That is the
real security boundary.

Views are optional. Use them when you want to hide columns (for example
omit `salary` from a staff view) or rename cryptic names. If you do, list
the hidden names in `SENSITIVE_IDENTIFIERS` so `execute_sql` cannot reach
around the view.

### Step 3 — bilingual names (optional)

If people or companies are stored in Arabic (or another script) and users
type English, add a `name_norm` column and search that, not `name_ar` alone.

`setup_demo.py` stores both scripts in one column:

```
name_norm = folded_arabic + " | " + english.lower()
```

Without that, “Ahmed Hassan” will not match `أحمد حسن`.

### Step 4 — test, then restart

```powershell
python mcp_client.py
python webapp.py
```

`python mcp_client.py` starts DBHub, lists its tools, prints the schema
snapshot, and runs one guarded SELECT. `/api/health` should show DBHub
connected. Suggested prompts on the empty screen come from the live schema.

---

## Environment setup

Set these once, permanently:

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", "D:\ollama\models", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_NO_CLOUD", "1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "30m", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "2", "User")
```

`OLLAMA_NO_CLOUD=1` is a machine-level guarantee that nothing routes to
Ollama's servers.

Keep the database password out of the code:

```powershell
[Environment]::SetEnvironmentVariable("DB_URL", "postgresql+psycopg://...", "User")
```

---

## Security controls, and where each lives

| Control | Enforced in |
|---|---|
| Read-only access | Database user grant + DBHub `readonly` |
| Row limit (50) | `dbhub.toml` + `guard_sql` cap |
| SELECT-only | `mcp_client.guard_sql` |
| Sensitive names gated | `SENSITIVE_IDENTIFIERS` + privacy toggle |
| No invented numbers | Grounding check in `agent.py` |
| Audit trail | `audit.log`, one JSON line per call |

**None of these depend on the model behaving well.** That is the point.

```powershell
Get-Content audit.log -Tail 20
```

---

## What to build next

1. **Your eval set.** 30–50 real questions with the correct answers written
   down. Tool-choice tests can pass while the SQL is still wrong.
2. **A gateway** (LiteLLM) in front of Ollama, for per-user auth and a second
   audit trail.
3. **Per-user sessions.** Right now `allow_sensitive` is a single flag. Tie it
   to the authenticated user before anyone touches real salary data.

---

## Troubleshooting

**DBHub will not start** → `npm install`, then `python mcp_client.py`. Node
must be 22.5 or newer (this repo pins 22.22.0 under `node_modules`).

**A second webapp on port 5000** → stop the old `python webapp.py` first.
Windows can let both bind; the old process keeps answering.

**execute_sql refused** → one SELECT only. Writes, PRAGMA, EXPLAIN, and extra
statements are blocked. Sensitive names in `SENSITIVE_IDENTIFIERS` need the
privacy toggle. If `ALLOWED_OBJECTS` is set, FROM/JOIN must stay inside it.

**Model invents numbers** → check `audit.log` for the last `execute_sql`
result. Usually the query returned nothing useful and the model filled the gap.

**Slow responses** → `ollama ps` shows whether the model is loaded on GPU or
CPU. One question is two generations (write SQL, then write the answer).
CPU inference on a 12B model is minutes, not seconds.

**No chart** → a single total is shown as a number on purpose. Ask for a
split (by region, by month) so there are two or more groups to draw.
