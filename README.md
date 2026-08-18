# Company Data Agent

Ask questions in Arabic or English, get real answers from the database.
Everything runs locally. No data leaves the machine.

---

## How the pieces connect

```
  you type a question
         │
         ▼
   agent.py ──────────► Ollama (gemma4:12b)   local, port 11434
         │  ▲                    │
         │  └── tool call ───────┘
         ├──────────── db_tools.py     curated tools (common questions)
         └──────────── mcp_client.py ► DBHub MCP server ► demo.db
                           │
                      guard_sql()      SELECT-only, views only, salary gate
```

DBHub is the real MCP database server. `mcp_client.py` launches it as a
read-only child process. The six curated tools still handle ordinary
questions; `execute_sql` is for dates and breakdowns those tools cannot
express. The grounding check runs either way.

| File | What it is | Do you edit it? |
|---|---|---|
| `catalog.py` | Your metrics, entities, connection | **Yes — this is the one** |
| `dbhub.toml` | DBHub DSN, readonly, row cap | When pointing at a real DB |
| `db_tools.py` | Curated tools + security | Rarely |
| `mcp_client.py` | DBHub launcher + SQL guard | Rarely |
| `agent.py` | The Ollama loop + grounding check | Rarely |
| `setup_demo.py` | Builds a demo database | No |

---

## Run it today (demo data)

```powershell
python -m pip install -r requirements.txt
npm install
python setup_demo.py
python webapp.py
```

Then open http://127.0.0.1:5000. `npm install` pulls the pinned DBHub MCP
server. The UI binds to localhost only.

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
how is revenue calculated?
```

Watch the `->` lines: those are the tools being called. That's the agent loop
you designed, running for real.

---

## Chat UI

A minimal local web chat sits in front of the same agent loop, at
`webapp.py` + `static/`. It talks to Ollama and the database exactly the
way the CLI does -- `webapp.py` just forwards each question to
`agent.ask()` and renders the tool trace, the grounded answer, and any
chart the model requested (drawn from the tool's actual numbers, never
from anything the model typed in the `CHART:` line).

```powershell
pip install flask
python webapp.py
```

Then open http://127.0.0.1:5000. It binds to localhost only -- not
reachable from the network. There's a checkbox to allow salary lookups for
the current question; leave it unchecked by default.

---

## Point it at your real database

### Step 1 — make a read-only user

**PostgreSQL**
```sql
CREATE USER agent_ro WITH PASSWORD 'change-me';
GRANT CONNECT ON DATABASE yourdb TO agent_ro;
GRANT USAGE ON SCHEMA public TO agent_ro;
-- grant ONLY the views, never the tables
GRANT SELECT ON v_employees, v_customers, v_orders TO agent_ro;
```

**MySQL**
```sql
CREATE USER 'agent_ro'@'%' IDENTIFIED BY 'change-me';
GRANT SELECT ON yourdb.v_employees TO 'agent_ro'@'%';
GRANT SELECT ON yourdb.v_customers TO 'agent_ro'@'%';
GRANT SELECT ON yourdb.v_orders TO 'agent_ro'@'%';
```

This is the real security boundary. Everything else is defence in depth.

### Step 2 — create the views

Views are where masking lives. Build them so the sensitive columns are simply
not selectable:

```sql
CREATE VIEW v_employees AS
SELECT id, name_ar, name_en, name_norm, department, hired_on
FROM employees;              -- salary deliberately absent
```

Views also let you rename cryptic columns into business language, which the
model understands far better than `sal_amt_m`.

### Step 3 — add normalized search columns

This is what makes Arabic search work. For each searchable name column:

```sql
ALTER TABLE employees ADD COLUMN name_norm TEXT;

UPDATE employees SET name_norm =
  LOWER(
    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
      name_ar, 'أ','ا'), 'إ','ا'), 'آ','ا'), 'ة','ه'), 'ى','ي')
  ) || ' | ' || LOWER(name_en);

CREATE INDEX idx_emp_norm ON employees(name_norm);
```

Storing **both scripts in one column** is what lets a user type `Ahmed Hassan`
or `أحمد حسن` and get the same result. Keep it updated with a trigger, or
regenerate on a schedule.

### Step 4 — edit `catalog.py`

```python
DB_URL = "postgresql+psycopg://agent_ro:pass@dbhost:5432/yourdb"
```

Then rewrite `ENTITIES`, `METRICS` and `PERIODS` for your data. Start with
**three metrics and two entities.** Add more only after those work.

Adjust `PERIODS` to your fiscal calendar — the dates in the demo are fixed
strings, so update them or compute them from today's date.

### Step 5 — install the driver

| Database | Package |
|---|---|
| PostgreSQL | `pip install psycopg[binary]` |
| MySQL | `pip install pymysql` |
| SQL Server | `pip install pyodbc` |
| SQLite | built in |

### Step 6 — test before connecting the model

```python
python -c "import db_tools; s=db_tools.ToolSession(); print(s.list_data_areas())"
```

If that returns your metrics, the database layer works. Only then run `agent.py`.

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
Ollama's servers. Worth citing in the security review.

Keep the database password out of the code:

```powershell
[Environment]::SetEnvironmentVariable("DB_URL", "postgresql+psycopg://...", "User")
```
```python
import os
DB_URL = os.environ["DB_URL"]
```

---

## Security controls, and where each lives

| Control | Enforced in |
|---|---|
| Read-only access | Database user grant |
| Column masking | The views |
| Row limit (50) | `db_tools._select` |
| SELECT-only | `db_tools._select` |
| No SQL injection | Parameterized queries throughout |
| ID provenance | `ToolSession.get_records` |
| Sensitive entities gated | `allow_sensitive` flag |
| Bidi characters stripped | `db_tools._clean` |
| No invented numbers | Grounding check in `agent.py` |
| Audit trail | `audit.log`, one JSON line per call |

**None of these depend on the model behaving well.** That is the point.

Check the audit log any time:
```powershell
Get-Content audit.log -Tail 20
```

---

## DBHub (the MCP database server)

The agent launches DBHub itself. You do not start it by hand.

```powershell
python mcp_client.py          # smoke test: start DBHub, list tools, one query
```

To point at a real database, edit `dbhub.toml` (`dsn`) and `catalog.py`
(`DB_URL` plus entities/metrics) together. Keep `readonly = true`.

---

## What to build next

1. **Your eval set.** 30–50 real questions **with the correct answers written
   down.** Your tests so far check tool choice, not correctness — that gap is
   how a system scores 100% and still returns "not found" for half your staff.
2. **Chart rendering.** The agent already emits a `CHART:` spec. Render it in
   your frontend — never execute model-generated plotting code.
3. **A gateway** (LiteLLM) in front of Ollama, for per-user auth and a second
   audit trail.
4. **Per-user sessions.** Right now `allow_sensitive` is a single flag. Tie it
   to the authenticated user before anyone touches real salary data.

---

## Troubleshooting

**"No module named sqlalchemy"** → `python -m pip install sqlalchemy`

**Search returns nothing** → the `name_norm` column is missing or not populated.
Check with `SELECT name_norm FROM v_employees LIMIT 5`.

**"id X was not returned by search_entities"** → working as intended. The model
tried to use an ID it never received. It should search first.

**Model invents numbers** → check `audit.log` to see what the tools actually
returned. Usually the tool returned nothing useful and the model filled the gap.

**Slow responses** → `ollama ps` shows whether the model is loaded on GPU or
CPU. CPU inference on a 12B model is minutes, not seconds.
