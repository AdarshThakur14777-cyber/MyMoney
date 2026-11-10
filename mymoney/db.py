"""SQLite layer: connection, schema, small query helpers.
All money is stored as integer paise. Every user-owned row carries user_id and
every query filters by it (per-user isolation).
"""
from __future__ import annotations

import os
import sqlite3

from flask import g

from .utils import now_ist_str

DB_PATH = os.environ.get(
    "MYMONEY_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "instance", "mymoney.db"))

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash     TEXT NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'INR',
    created_at  TEXT NOT NULL,
    last_login  TEXT,
    force_pw    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS people (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    phone        TEXT,
    upi_id       TEXT,
    relationship TEXT,
    notes        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_people_user_name
    ON people(user_id, name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS loans (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    person_id      INTEGER REFERENCES people(id) ON DELETE SET NULL,
    side           TEXT NOT NULL CHECK (side IN ('given','taken')),
    amount_paise   INTEGER NOT NULL CHECK (amount_paise > 0),
    disbursed_date TEXT NOT NULL,
    due_date       TEXT,
    interest_pct   REAL NOT NULL DEFAULT 0,
    notes          TEXT,
    is_sample      INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    person_id       INTEGER REFERENCES people(id) ON DELETE SET NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('in','out')),
    amount_paise    INTEGER NOT NULL CHECK (amount_paise > 0),
    tx_date         TEXT NOT NULL,
    tx_time         TEXT,
    nature          TEXT NOT NULL CHECK (nature IN (
        'expense','refund','other_income','loan_given','loan_taken',
        'repayment_in','repayment_out','gift_given','gift_received','transfer')),
    linked_loan_id  INTEGER REFERENCES loans(id) ON DELETE SET NULL,
    refund_of       INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    method          TEXT,
    utr             TEXT,
    category        TEXT,
    subcategory     TEXT,
    description     TEXT,
    notes           TEXT,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','cancelled')),
    is_sample       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_user_date
    ON transactions(user_id, tx_date);
CREATE INDEX IF NOT EXISTS idx_tx_user_loan
    ON transactions(user_id, linked_loan_id);
CREATE INDEX IF NOT EXISTS idx_tx_user_utr
    ON transactions(user_id, utr);
CREATE INDEX IF NOT EXISTS idx_tx_user_person
    ON transactions(user_id, person_id);

CREATE TABLE IF NOT EXISTS txn_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    txn_id      INTEGER NOT NULL,
    changed_at  TEXT NOT NULL,
    actor_id    INTEGER NOT NULL REFERENCES users(id),
    action      TEXT NOT NULL,             -- create | edit | cancel | attachment | restore
    changes     TEXT,                      -- JSON {field: {old, new}}
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_hist_txn ON txn_history(user_id, txn_id);

CREATE TABLE IF NOT EXISTS attachments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    txn_id      INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    orig_name   TEXT NOT NULL,
    stored_name TEXT NOT NULL UNIQUE,
    mime        TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_att_txn ON attachments(txn_id);

CREATE TABLE IF NOT EXISTS settings (
    user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    alert_large_paise INTEGER NOT NULL DEFAULT 1000000,      -- ₹10,000 in paise
    include_sample    INTEGER NOT NULL DEFAULT 1,
    reminded_sample   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS category_budgets (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category     TEXT NOT NULL COLLATE NOCASE,
    limit_paise  INTEGER NOT NULL CHECK (limit_paise > 0),
    PRIMARY KEY (user_id, category)
);

CREATE TABLE IF NOT EXISTS list_items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    list_name TEXT NOT NULL,                     -- category | payment_method
    value     TEXT NOT NULL,
    sort      INTEGER NOT NULL DEFAULT 100,
    UNIQUE (user_id, list_name, value COLLATE NOCASE)
);
"""


def connect(db_path: str = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def init_db(db_path: str = None) -> None:
    con = connect(db_path)
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


# --- flask request-scoped connection ----------------------------------------
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_e=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def q(sql: str, args: tuple = (), one: bool = False):
    """SELECT helper returning dicts."""
    cur = get_db().execute(sql, args)
    rows = [dict(r) for r in cur.fetchall()]
    return (rows[0] if rows else None) if one else rows


def execute(sql: str, args: tuple = ()) -> int:
    cur = get_db().execute(sql, args)
    get_db().commit()
    return cur.lastrowid


def stamp_update():
    """updated_at value for edits."""
    return now_ist_str()
