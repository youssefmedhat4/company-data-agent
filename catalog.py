#!/usr/bin/env python3
"""
Connection and policy for whichever database you point this agent at.

The agent does not hard-code your tables. It asks DBHub for the schema
(search_objects) and reads data with execute_sql.

  1. DB_URL                  -- how to connect (or set the DB_URL env var)
  2. SENSITIVE_IDENTIFIERS   -- table/view/column names that need the privacy toggle
  3. ALLOWED_OBJECTS         -- optional allow-list; empty means every table/view
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------
# 1. CONNECTION
#
# Use a READ-ONLY database user. Not your admin account.
#
#   SQLite      sqlite:///demo.db
#   PostgreSQL  postgresql+psycopg://readonly_user:pass@host:5432/dbname
#   MySQL       mysql+pymysql://readonly_user:pass@host:3306/dbname
#   SQL Server  mssql+pyodbc://readonly_user:pass@host/db?driver=ODBC+Driver+18+for+SQL+Server
# ---------------------------------------------------------------
DB_URL = os.environ.get("DB_URL", "sqlite:///demo.db")


# ---------------------------------------------------------------
# 2. SENSITIVE IDENTIFIERS
#
# Lowercased names of tables, views, or columns that execute_sql and
# search_objects must not expose unless allow_sensitive=True.
# Empty = nothing extra is gated (writes are still blocked).
# ---------------------------------------------------------------
def _csv(name: str, default: str = "") -> tuple[str, ...]:
    raw = os.environ.get(name, default)
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


SENSITIVE_IDENTIFIERS = _csv("SENSITIVE_IDENTIFIERS", "")


# ---------------------------------------------------------------
# 3. OPTIONAL ALLOW-LIST
#
# If non-empty, execute_sql may only FROM/JOIN these objects.
# Empty = any table or view DBHub can see (minus sqlite internals).
# ---------------------------------------------------------------
ALLOWED_OBJECTS = _csv("ALLOWED_OBJECTS", "")


# ---------------------------------------------------------------
# 4. SAFETY LIMITS
# ---------------------------------------------------------------
MAX_ROWS = 50
QUERY_TIMEOUT_SEC = 10
AUDIT_LOG = "audit.log"
