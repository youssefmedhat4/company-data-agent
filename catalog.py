#!/usr/bin/env python3
"""
CATALOG — the only file you edit when pointing at a new database.

Everything else is generic. This file describes YOUR data in business
language, which is what makes the model understand it.

Three things to fill in:
  1. DB_URL              -- how to connect
  2. ENTITIES            -- things that can be searched by name
  3. METRICS             -- aggregated figures that can be requested
"""

# ---------------------------------------------------------------
# 1. CONNECTION
#
# Use a READ-ONLY database user. Not your admin account.
#
#   SQLite      sqlite:///demo.db
#   PostgreSQL  postgresql+psycopg://readonly_user:pass@host:5432/dbname
#   MySQL       mysql+pymysql://readonly_user:pass@host:3306/dbname
#   SQL Server  mssql+pyodbc://readonly_user:pass@host/db?driver=ODBC+Driver+18+for+SQL+Server
#
# Better: read it from an environment variable so no password is in code.
#   import os; DB_URL = os.environ["DB_URL"]
# ---------------------------------------------------------------
DB_URL = "sqlite:///demo.db"


# ---------------------------------------------------------------
# 2. ENTITIES — things a user might name in a question
#
# view          : the curated view to read (never a raw table)
# id_column     : primary key
# search_column : the NORMALIZED column used for matching
# display_columns: what to show the user
# detail_columns : what get_records may return
# sensitive     : if True, requires allow_sensitive=True at call time
# filterable    : columns list_entities may filter on, as {argument: column}.
#                 This is a whitelist -- anything absent cannot be filtered,
#                 which is what keeps "list rows" from becoming "run any query".
# ---------------------------------------------------------------
ENTITIES = {
    "employee": {
        "view": "v_employees",
        "id_column": "id",
        "search_column": "name_norm",
        "display_columns": ["id", "name_ar", "name_en", "department"],
        "detail_columns": ["id", "name_ar", "name_en", "department", "hired_on"],
        "description": "Staff members of the company.",
        "filterable": {"department": "department"},
    },
    "employee_salary": {
        "view": "v_employees_sensitive",
        "id_column": "id",
        "search_column": "name_norm",
        "display_columns": ["id", "name_ar", "name_en", "department"],
        "detail_columns": ["id", "name_ar", "name_en", "department", "salary"],
        "description": "Staff salary records. Restricted.",
        "sensitive": True,
        "filterable": {"department": "department"},
        # Same people, same ids, different columns. Without this an id obtained
        # from "employee" is refused here, even though it is the same row.
        "shares_ids_with": "employee",
    },
    "customer": {
        "view": "v_customers",
        "id_column": "id",
        "search_column": "name_norm",
        "display_columns": ["id", "name_ar", "name_en", "region"],
        "detail_columns": ["id", "name_ar", "name_en", "region"],
        "description": "Companies that place orders.",
        "filterable": {"region": "region"},
    },
}


# ---------------------------------------------------------------
# 3. METRICS — aggregated figures
#
# Every metric is a fixed SQL fragment. The model chooses WHICH metric
# and WHICH period, never how it is calculated.
#
# date_column : the column the period filter applies to. Set to None for a
#               figure that describes the present rather than a span of time
#               (a payroll total is a snapshot; revenue is not). The period
#               argument is then ignored and the result says so.
# sensitive   : if True, requires allow_sensitive=True at call time, exactly
#               like a sensitive entity.
# ---------------------------------------------------------------
METRICS = {
    "revenue": {
        "view": "v_orders",
        "expression": "SUM(amount)",
        "date_column": "order_date",
        "unit": "EGP",
        "definition": "Gross order value before returns and tax.",
        "group_by_options": {"none": None, "region": "region", "month": "substr(order_date,1,7)"},
        "filter_by": {"customer": "customer_id"},
    },
    "order_count": {
        "view": "v_orders",
        "expression": "COUNT(*)",
        "date_column": "order_date",
        "unit": "orders",
        "definition": "Number of orders placed.",
        "group_by_options": {"none": None, "region": "region", "month": "substr(order_date,1,7)"},
        "filter_by": {"customer": "customer_id"},
    },
    "avg_order_value": {
        "view": "v_orders",
        "expression": "AVG(amount)",
        "date_column": "order_date",
        "unit": "EGP",
        "definition": "Mean order value over the period.",
        "group_by_options": {"none": None, "region": "region", "month": "substr(order_date,1,7)"},
        "filter_by": {"customer": "customer_id"},
    },
    # "How many people work in Finance?" means today, not "hired between two
    # dates" -- as a period metric this returned 0 for every period, because
    # nobody happened to be hired inside one. Hiring over time is "hires" below.
    "headcount": {
        "view": "v_employees",
        "expression": "COUNT(*)",
        "date_column": None,
        "unit": "people",
        "definition": "People currently employed. A present-day count.",
        "group_by_options": {"none": None, "department": "department"},
        "filter_by": {},
    },
    "hires": {
        "view": "v_employees",
        "expression": "COUNT(*)",
        "date_column": "hired_on",
        "unit": "people",
        "definition": "People hired during the period.",
        "group_by_options": {"none": None, "department": "department"},
        "filter_by": {},
    },
    "total_salary": {
        "view": "v_employees_sensitive",
        "expression": "SUM(salary)",
        "date_column": None,
        "unit": "EGP",
        "definition": ("Combined salary of everyone currently employed. A present-day "
                       "total, so it is not scoped to a time period."),
        "group_by_options": {"none": None, "department": "department"},
        "filter_by": {},
        "sensitive": True,
    },
}


# ---------------------------------------------------------------
# 4. PERIODS — fixed, so the model cannot invent date ranges
# Values are (start, end) as ISO strings, or None for open-ended.
# Adjust to your fiscal calendar.
# ---------------------------------------------------------------
PERIODS = {
    "last_month":   ("2026-07-01", "2026-07-31"),
    "last_quarter": ("2026-04-01", "2026-06-30"),
    "ytd":          ("2026-01-01", "2026-12-31"),
    "last_year":    ("2025-01-01", "2025-12-31"),
}


# ---------------------------------------------------------------
# 5. RAW TABLES — named here only so they can be REFUSED
#
# The views above are where column masking lives, so a query that reads a
# base table directly steps around it. mcp_client.guard_sql() refuses any
# object that is not one of the views listed in ENTITIES/METRICS, which
# already covers these; they are listed explicitly as well because a table
# holding restricted columns must be refused by name even if someone later
# adds a view over it. List every base table that carries sensitive data.
# ---------------------------------------------------------------
RAW_TABLES = ("employees",)


# ---------------------------------------------------------------
# 6. SAFETY LIMITS
#
# MAX_ROWS is enforced in three places: db_tools._select for the curated
# tools, dbhub.toml's max_rows for DBHub's own cap, and agent.py when it
# truncates a DBHub result. Change it here and in dbhub.toml together --
# mcp_client refuses to start if the two disagree.
# ---------------------------------------------------------------
MAX_ROWS = 50            # never return more than this
QUERY_TIMEOUT_SEC = 10   # kill long-running queries
AUDIT_LOG = "audit.log"  # every tool call is written here
