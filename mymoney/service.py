"""Domain logic shared by routes: balances, loan status, dedupe checks, audit.
Money stays integer paise end-to-end.
"""
from __future__ import annotations

import json

from .auth import csrf_token  # noqa: F401  (re-export convenience)
from .db import execute, q, stamp_update
from .utils import (NATURES, compact, fmt_money, parse_date, today_ist)

LOAN_FIELD_WHITELIST = ("direction", "amount_paise", "tx_date", "tx_time",
                        "nature", "linked_loan_id", "refund_of", "method",
                        "utr", "category", "subcategory", "description",
                        "notes", "person_id", "status")


# --------------------------------------------------------------------------
# balances & loan state
# --------------------------------------------------------------------------
def loan_repayment_total(uid: int, loan_id: int, is_sample: int) -> int:
    """Paise repaid against this loan, matching the loan's sample flag."""
    row = q(
        """SELECT TOTAL(t.amount_paise) AS tot
             FROM transactions t
            WHERE t.user_id = ? AND t.status = 'active'
              AND t.linked_loan_id = ?
              AND (t.nature = 'repayment_in' OR t.nature = 'repayment_out')
              AND t.is_sample = ?""",
        (uid, loan_id, is_sample), one=True)
    return int(row["tot"] or 0) if row else 0


def loan_view(uid: int, loan: dict, person_name: str | None = None) -> dict:
    """Decorate a loan row with repayment total / balance / status / person."""
    rep = loan_repayment_total(uid, loan["id"], loan["is_sample"])
    balance = max(0, loan["amount_paise"] - rep)
    today = today_ist().isoformat()
    if balance <= 0:
        status = "Settled"
    elif loan["due_date"] and loan["due_date"] < today:
        status = "Overdue"
    elif rep > 0:
        status = "Partially Repaid"
    else:
        status = "Open"
    out = dict(loan)
    out.update(repaid_paise=rep, balance_paise=balance, status=status,
               person_name=person_name or "",
               overdue_days=(parse_date(today) - parse_date(loan["due_date"])
                             ).days if (status == "Overdue") else 0)
    return out


def loan_balances(uid: int, include_sample: int = 1) -> dict:
    """Aggregates: lent outstanding, borrowed outstanding, overdue lists."""
    loans = all_loans(uid, include_sample)
    lent = [d for d in loans if d["side"] == "given"]
    taken = [d for d in loans if d["side"] == "taken"]
    overdue = [d for d in loans if d["status"] == "Overdue"]
    return {
        "lent_outstanding": sum(d["balance_paise"] for d in lent),
        "borrowed_outstanding": sum(d["balance_paise"] for d in taken),
        "open_given": [d for d in lent if d["status"] != "Settled"],
        "open_taken": [d for d in taken if d["status"] != "Settled"],
        "overdue": overdue,
    }


def all_loans(uid: int, include_sample: int = 1) -> list[dict]:
    where = "" if include_sample else " AND l.is_sample = 0"
    loans = q(f"""
        SELECT l.*, p.name AS person_name,
               (SELECT COUNT(*) FROM transactions t
                 WHERE t.linked_loan_id = l.id AND t.user_id = l.user_id
                   AND t.status = 'active') AS n_tx
          FROM loans l LEFT JOIN people p ON p.id = l.person_id
         WHERE l.user_id = ?{where}
         ORDER BY l.disbursed_date DESC, l.id DESC""", (uid,))
    return [loan_view(uid, row, row.get("person_name")) for row in loans]


def get_loan(uid: int, loan_id: int) -> dict | None:
    row = q("""SELECT l.*, p.name AS person_name
                 FROM loans l LEFT JOIN people p ON p.id = l.person_id
                WHERE l.user_id = ? AND l.id = ?""", (uid, loan_id), one=True)
    return loan_view(uid, row, row.get("person_name")) if row else None


# --------------------------------------------------------------------------
# transaction helpers
# --------------------------------------------------------------------------
def get_txn(uid: int, txn_id: int) -> dict | None:
    return q("""SELECT t.*, p.name AS person_name,
                       l.id AS loan_id, l.side AS loan_side,
                       l.amount_paise AS loan_amount, l.due_date AS loan_due,
                       pl.name AS loan_person
                  FROM transactions t
             LEFT JOIN people p ON p.id = t.person_id
             LEFT JOIN loans l ON l.id = t.linked_loan_id
             LEFT JOIN people pl ON pl.id = l.person_id
                 WHERE t.user_id = ? AND t.id = ?""", (uid, txn_id), one=True)


def list_txns(uid: int, f: dict, include_sample: int = 1,
              limit: int = 500) -> list[dict]:
    """Filtered transaction list. f: month/year/direction/nature/person/
    category/q/sample (subset)."""
    sql = """SELECT t.*, p.name AS person_name, lp.name AS loan_person
               FROM transactions t
          LEFT JOIN people p ON p.id = t.person_id
          LEFT JOIN loans l ON l.id = t.linked_loan_id
          LEFT JOIN people lp ON lp.id = l.person_id
              WHERE t.user_id = ? AND t.status = 'active'"""
    args: list = [uid]
    if not include_sample:
        sql += " AND t.is_sample = 0"
    if f.get("sample") == "sample_only":
        sql += " AND t.is_sample = 1"
    if f.get("month") and f["month"] != "all":
        sql += " AND substr(t.tx_date,1,7) = ?"
        args.append(f["month"])
    if f.get("year") and f["year"] != "all":
        sql += " AND substr(t.tx_date,1,4) = ?"
        args.append(f["year"])
    if f.get("direction") in ("in", "out"):
        sql += " AND t.direction = ?"
        args.append(f["direction"])
    if f.get("nature"):
        sql += " AND t.nature = ?"
        args.append(f["nature"])
    if f.get("person_id"):
        sql += " AND t.person_id = ?"
        args.append(int(f["person_id"]))
    if f.get("category") and f["category"] != "all":
        sql += " AND t.category = ?"
        args.append(f["category"])
    if f.get("q"):
        like = "%" + f["q"].strip() + "%"
        sql += (" AND (t.description LIKE ? OR t.notes LIKE ? OR t.utr LIKE ?"
                " OR p.name LIKE ? OR t.category LIKE ? OR t.subcategory LIKE ?"
                " OR t.upi_id LIKE ?)")
        args += [like] * 7
    sql += " ORDER BY t.tx_date DESC, t.id DESC LIMIT ?"
    args.append(int(limit))
    return q(sql, tuple(args))


def find_duplicates(uid: int, *, amount_paise: int, tx_date: str,
                    person_id: int | None, utr: str,
                    exclude_id: int | None = None,
                    natures: tuple = ("expense", "transfer", "gift_given")) -> dict:
    """Warning-level duplicate check (used by the save path).
    Flags (a) reused UTRs and (b) same person/date/amount double-entry."""
    out = {"utr": [], "same_day": []}
    if utr:
        sql = ("SELECT * FROM transactions WHERE user_id = ? "
               "AND status = 'active' AND utr = ?")
        args: list = [uid, utr]
        if exclude_id:
            sql += " AND id != ?"
            args.append(exclude_id)
        out["utr"] = q(sql, tuple(args))
    if amount_paise and tx_date:
        sql = (f"SELECT * FROM transactions WHERE user_id = ? "
               f"AND status = 'active' AND amount_paise = ? AND tx_date = ? "
               f"AND nature IN ({','.join('?' * len(natures))})")
        args = [uid, int(amount_paise), tx_date, *natures]
        if person_id:
            sql += " AND person_id = ?"
            args.append(int(person_id))
        if exclude_id:
            sql += " AND id != ?"
            args.append(exclude_id)
        out["same_day"] = q(sql, tuple(args))
    return out


def add_history(uid: int, txn_id: int, actor_id: int, action: str,
                changes: dict | None = None, note: str | None = None) -> None:
    execute(
        "INSERT INTO txn_history (user_id, txn_id, changed_at, actor_id,"
        " action, changes, note) VALUES (?,?,?,?,?,?,?)",
        (uid, txn_id, stamp_update(), actor_id, action,
         json.dumps(changes, ensure_ascii=False) if changes else None, note))


def txn_history(uid: int, txn_id: int) -> list[dict]:
    return q("""SELECT h.*, u.username AS actor
                  FROM txn_history h JOIN users u ON u.id = h.actor_id
                 WHERE h.user_id = ? AND h.txn_id = ?
                 ORDER BY h.changed_at DESC, h.id DESC""", (uid, txn_id))


def recent_audit(uid: int, limit: int = 100) -> list[dict]:
    return q("""SELECT h.*, u.username AS actor, t.description AS tdesc
                  FROM txn_history h
                  JOIN users u ON u.id = h.actor_id
             LEFT JOIN transactions t ON t.id = h.txn_id
                 WHERE h.user_id = ?
                 ORDER BY h.changed_at DESC, h.id DESC LIMIT ?""",
             (uid, int(limit)))


def attach_count(uid: int, txn_id: int) -> int:
    row = q("SELECT COUNT(*) AS n FROM attachments WHERE user_id = ? AND txn_id = ?",
            (uid, txn_id), one=True)
    return int(row["n"]) if row else 0


def attachments_for(uid: int, txn_id: int) -> list[dict]:
    return q("""SELECT * FROM attachments
                 WHERE user_id = ? AND txn_id = ?
                 ORDER BY uploaded_at""", (uid, txn_id))


# --------------------------------------------------------------------------
# reminders
# --------------------------------------------------------------------------
def reminders(uid: int, include_sample: int = 1) -> dict:
    """Small computed task list: overdue loans, due soon, data hygiene."""
    bal = loan_balances(uid, include_sample)
    today = today_ist()
    soon = []
    for kind, group in (("loan_in", bal["open_given"]),
                        ("loan_out", bal["open_taken"])):
        for ln in group:
            if ln["due_date"]:
                dd = parse_date(ln["due_date"])
                if dd and 0 <= (dd - today).days <= 7:
                    soon.append({"kind": kind, "loan": ln})
    missing_desc = q("""SELECT COUNT(*) AS n FROM transactions
                         WHERE user_id = ? AND status = 'active'
                           AND nature = 'expense' AND amount_paise > 0
                           AND (description IS NULL OR TRIM(description) = '')""",
                     (uid,), one=True)
    sample_clause = "" if include_sample else " AND is_sample = 0"
    dup_utrs = q(f"""SELECT TOTAL(c) AS n FROM (
                        SELECT COUNT(*) AS c
                          FROM transactions
                         WHERE user_id = ? AND status = 'active'
                           AND COALESCE(utr,'') != ''{sample_clause}
                         GROUP BY utr HAVING COUNT(*) > 1)""",
                 (uid,), one=True)
    from .budgets import monthly_budgets
    currency = q("SELECT currency FROM users WHERE id = ?", (uid,), one=True)
    budget_alerts = [b for b in monthly_budgets(
        uid, include_sample, currency["currency"] if currency else "INR")
        if b["status"] != "ok"]
    return {"overdue": bal["overdue"], "due_soon": soon,
            "budget_alerts": budget_alerts,
            "missing_desc": int((missing_desc or {}).get("n", 0)),
            "dup_utrs": int((dup_utrs or {}).get("n", 0))}
