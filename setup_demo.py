#!/usr/bin/env python3
"""
Creates a small demo database (SQLite) with Arabic and English data,
including the normalized shadow columns your real database will need.

Run this once:
    python setup_demo.py

It writes demo.db in the current folder. Nothing else touches your real
database until you edit catalog.py.
"""

import sqlite3
import os

DB = "demo.db"


def normalize_ar(text: str) -> str:
    """Fold Arabic orthographic variants so search matches any spelling."""
    out = (text or "").strip().lower()
    for src, dst in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي"),
                     ("ـ", ""), ("ً", ""), ("ٌ", ""), ("ٍ", ""), ("َ", ""),
                     ("ُ", ""), ("ِ", ""), ("ّ", ""), ("ْ", "")):
        out = out.replace(src, dst)
    return " ".join(out.split())


EMPLOYEES = [
    (8823, "أحمد حسن", "Ahmed Hassan", "Finance", 45000, "2019-03-01"),
    (9104, "أحمد حسن", "Ahmed Hassan", "Sales", 38000, "2021-07-15"),
    (7201, "سارة إبراهيم", "Sara Ibrahim", "Finance", 52000, "2018-01-20"),
    (7455, "منى عبد الله", "Mona Abdullah", "Operations", 41000, "2020-11-02"),
]

CUSTOMERS = [
    (3301, "شركة النيل للتجارة", "Nile Trading Co", "Cairo"),
    (3302, "دلتا للأغذية", "Delta Foods", "Alexandria"),
    (3303, "المعادي للتوريدات", "Maadi Supplies", "Cairo"),
]

ORDERS = [
    (1, 3301, "2026-05-12", 24500), (2, 3301, "2026-06-03", 18200),
    (3, 3302, "2026-05-28", 31000), (4, 3303, "2026-06-19", 12750),
    (5, 3301, "2026-07-02", 29800), (6, 3302, "2026-07-14", 22400),
    (7, 3303, "2026-07-30", 16900), (8, 3301, "2026-08-05", 33100),
]


def main() -> None:
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE employees (
            id INTEGER PRIMARY KEY, name_ar TEXT, name_en TEXT,
            name_norm TEXT, department TEXT, salary INTEGER, hired_on TEXT)
    """)
    cur.execute("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY, name_ar TEXT, name_en TEXT,
            name_norm TEXT, region TEXT)
    """)
    cur.execute("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY, customer_id INTEGER,
            order_date TEXT, amount INTEGER)
    """)

    for eid, ar, en, dept, sal, hired in EMPLOYEES:
        # both scripts folded into one searchable column
        norm = normalize_ar(ar) + " | " + en.lower()
        cur.execute("INSERT INTO employees VALUES (?,?,?,?,?,?,?)",
                    (eid, ar, en, norm, dept, sal, hired))

    for cid, ar, en, region in CUSTOMERS:
        norm = normalize_ar(ar) + " | " + en.lower()
        cur.execute("INSERT INTO customers VALUES (?,?,?,?,?)",
                    (cid, ar, en, norm, region))

    cur.executemany("INSERT INTO orders VALUES (?,?,?,?)", ORDERS)

    cur.execute("CREATE INDEX idx_emp_norm ON employees(name_norm)")
    cur.execute("CREATE INDEX idx_cust_norm ON customers(name_norm)")

    # Curated views: the ONLY thing the tools are allowed to read.
    # Note salary is NOT in the public employee view.
    cur.execute("""
        CREATE VIEW v_employees AS
        SELECT id, name_ar, name_en, name_norm, department, hired_on
        FROM employees
    """)
    cur.execute("""
        CREATE VIEW v_employees_sensitive AS
        SELECT id, name_ar, name_en, name_norm, department, salary
        FROM employees
    """)
    cur.execute("""
        CREATE VIEW v_customers AS
        SELECT id, name_ar, name_en, name_norm, region FROM customers
    """)
    cur.execute("""
        CREATE VIEW v_orders AS
        SELECT o.id, o.customer_id, c.name_ar AS customer_name,
               c.region, o.order_date, o.amount
        FROM orders o JOIN customers c ON c.id = o.customer_id
    """)

    con.commit()
    con.close()
    print(f"Created {DB}")
    print(f"  {len(EMPLOYEES)} employees, {len(CUSTOMERS)} customers, {len(ORDERS)} orders")
    print("  Views: v_employees, v_employees_sensitive, v_customers, v_orders")


if __name__ == "__main__":
    main()
