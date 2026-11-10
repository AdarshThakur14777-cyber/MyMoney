"""Dashboard + JSON API for charts (vanilla-SVG renderer in static/js)."""
from __future__ import annotations

import datetime as _dt
import json

from flask import Blueprint, request, render_template, jsonify

from . import service
from .budgets import monthly_budgets
from .auth import current_user, login_required
from .db import q
from .utils import fmt_money, ist_fmt_date, ist_fmt_ts, today_ist

bp = Blueprint("dashboard", __name__)

RANGES = {"1m": 30, "3m": 90, "1y": 365, "all": None}
RANGE_LABEL = {"1m": "last 30 days", "3m": "last 90 days",
               "1y": "last 365 days", "all": "all time"}
MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _as_date(d):
    if isinstance(d, _dt.datetime):
        return d.date()
    return d


def _shift_month(d: _dt.date, k: int) -> _dt.date:
    """First day of the month k offsets from the month of d."""
    tot = d.year * 12 + (d.month - 1) + k
    return _dt.date(tot // 12, tot % 12 + 1, 1)


def _win(uid: int, inc: str, start: str | None, end: str | None) -> dict:
    sql = f"""SELECT TOTAL(CASE WHEN direction = 'in' THEN amount_paise
                                ELSE 0 END) AS i,
                     TOTAL(CASE WHEN direction = 'out' THEN amount_paise
                                ELSE 0 END) AS o,
                     TOTAL(CASE WHEN nature = 'expense' AND direction = 'out'
                                THEN amount_paise ELSE 0 END) AS e,
                     TOTAL(CASE WHEN nature = 'refund' AND direction = 'in'
                                THEN amount_paise ELSE 0 END) AS r,
                     COUNT(*) AS n
                FROM transactions
               WHERE user_id = ? AND status = 'active'{inc}"""
    args = [uid]
    if start:
        sql += " AND tx_date >= ?"
        args.append(start)
    if end:
        sql += " AND tx_date <= ?"
        args.append(end)
    r = q(sql, args, one=True)
    i, o, e, rr = r["i"], r["o"], r["e"], r["r"]
    return {"in": i, "out": o, "exp": e - rr, "net": i - o, "n": int(r["n"])}


def _delta(cur: int, prev: int, span: str) -> str:
    if prev == 0:
        return "no activity in previous period"
    p = (cur - prev) * 100.0 / abs(prev)
    arrow = "↑" if p >= 0 else "↓"
    return f"{arrow} {abs(p):.0f}% vs previous {span}"


@bp.get("/")
@login_required
def home():
    uid = current_user()["id"]
    st = q("SELECT include_sample FROM settings WHERE user_id = ?", (uid,),
           one=True) or {"include_sample": 1}
    cur = current_user()["currency"]
    inc = " AND 1" if st["include_sample"] else " AND is_sample = 0"

    rng = (request.args.get("range") or "3m").lower()
    if rng not in RANGES:
        rng = "3m"
    days = RANGES[rng]
    span = RANGE_LABEL[rng]

    today = _as_date(today_ist())
    if days is None:                       # all time
        start = prev_start = None
        end = prev_end = None
    else:
        start = (today - _dt.timedelta(days=days)).isoformat()
        end = today.isoformat()
        prev_start = (today - _dt.timedelta(days=2 * days)).isoformat()
        prev_end = (today - _dt.timedelta(days=days + 1)).isoformat()

    w = _win(uid, inc, start, end)
    p = _win(uid, inc, prev_start, prev_end)
    saving = (w["in"] - w["exp"]) * 100 // w["in"] if w["in"] > 0 else 0
    psaving = ((p["in"] - p["exp"]) * 100 // p["in"]) if p["in"] > 0 else 0
    kpis = {
        "recv": fmt_money(w["in"], cur),
        "paid": fmt_money(w["out"], cur),
        "net": fmt_money(w["net"], cur),
        "exp": fmt_money(w["exp"], cur),
        "saving": saving,
        "saving_prev": psaving,
        "d_recv": _delta(w["in"], p["in"], span),
        "d_paid": _delta(w["out"], p["out"], span),
        "d_exp": _delta(w["exp"], p["exp"], span),
        "d_net": _delta(w["net"], p["net"], span),
        "d_saving": _delta(saving, psaving, span),
        "n": w["n"],
    }

    # --- cash-flow chart: the last 6 calendar months ------------------------
    first = _shift_month(today.replace(day=1), -5)
    keys, labels = [], []
    for k in range(6):
        mk = _shift_month(first, k)
        keys.append("%04d-%02d" % (mk.year, mk.month))
        labels.append(MONTH_SHORT[mk.month - 1])
    series = {k: {"in": 0, "out": 0, "expense": 0, "refund": 0} for k in keys}
    for r in q(f"""SELECT substr(tx_date,1,7) AS mk, direction, nature,
                          amount_paise
                     FROM transactions
                    WHERE user_id = ? AND status = 'active'
                      AND tx_date >= ?{inc}""",
               (uid, first.isoformat())):
        if r["mk"] in series:
            d = series[r["mk"]]
            if r["direction"] == "in":
                d["in"] += r["amount_paise"]
                if r["nature"] == "refund":
                    d["refund"] += r["amount_paise"]
            else:
                d["out"] += r["amount_paise"]
                if r["nature"] == "expense":
                    d["expense"] += r["amount_paise"]
    series_json = json.dumps({
        "keys": keys, "labels": labels,
        "series": {k: {kk: v / 100 for kk, v in d.items()}
                   for k, d in series.items()}}, ensure_ascii=False)

    # --- spending donut by category (window scope) --------------------------
    sql = f"""SELECT category, TOTAL(amount_paise) AS tot, COUNT(*) AS n
                FROM transactions
               WHERE user_id = ? AND status = 'active'
                 AND nature = 'expense' AND direction = 'out'{inc}"""
    args = [uid]
    if start:
        sql += " AND tx_date >= ?"
        args.append(start)
    if end:
        sql += " AND tx_date <= ?"
        args.append(end)
    sql += " GROUP BY category ORDER BY tot DESC"
    cat_rows = q(sql, args)
    cats_json = json.dumps(
        [{"name": r["category"] or "Other", "value": round(r["tot"] / 100, 2)}
         for r in cat_rows], ensure_ascii=False)

    # --- recent transactions -------------------------------------------------
    recent = q(f"""SELECT t.id, t.tx_date, t.tx_time, t.nature, t.direction,
                          t.amount_paise, t.category, t.description,
                          t.is_sample, p.name AS person_name
                     FROM transactions t
                LEFT JOIN people p ON p.id = t.person_id
                    WHERE t.user_id = ? AND t.status = 'active'{inc}
                 ORDER BY t.tx_date DESC, t.id DESC LIMIT 6""", (uid,))

    # --- open loans as recovery goals ---------------------------------------
    loan_inc = "" if st["include_sample"] else " AND l.is_sample = 0"
    lsql = f"""SELECT l.id, l.side, l.amount_paise, l.due_date, l.is_sample,
                      COALESCE(p.name, '') AS person_name,
                      TOTAL(CASE WHEN t.linked_loan_id IS NOT NULL
                                 THEN t.amount_paise ELSE 0 END) AS repaid
                 FROM loans l
            LEFT JOIN people p ON p.id = l.person_id
            LEFT JOIN transactions t
                   ON t.linked_loan_id = l.id AND t.status = 'active'
                WHERE l.user_id = ?{loan_inc}
             GROUP BY l.id
             ORDER BY (l.due_date IS NULL), l.due_date, l.id"""
    loans_all = q(lsql, (uid,))
    goals, overdue_n, due_soon_n = [], 0, 0
    for l in loans_all:
        bal = l["amount_paise"] - l["repaid"]
        if bal <= 0:
            continue
        pct = l["repaid"] * 100 // l["amount_paise"] if l["amount_paise"] else 0
        od = bool(l["due_date"] and l["due_date"] < today.isoformat())
        soon = (not od and l["due_date"] and
                l["due_date"] <= (today + _dt.timedelta(days=7)).isoformat())
        overdue_n += od
        due_soon_n += soon
        goals.append({
            "id": l["id"], "side": l["side"],
            "name": l["person_name"] or ("Loan #%d" % l["id"]),
            "is_sample": l["is_sample"],
            "repaid_txt": fmt_money(l["repaid"], cur),
            "total_txt": fmt_money(l["amount_paise"], cur),
            "due_txt": ist_fmt_date(l["due_date"]) if l["due_date"] else "no due date",
            "pct": min(100, int(pct)),
            "cls": "late" if od else ("soon" if soon else "ok"),
        })
        if len(goals) >= 5:
            break
    bal = service.loan_balances(uid, st["include_sample"])
    rem = service.reminders(uid, st["include_sample"])
    budgets = monthly_budgets(uid, st["include_sample"], cur)

    samples = q("SELECT COUNT(*) AS n FROM transactions WHERE user_id = ?"
                " AND is_sample = 1 AND status='active'", (uid,), one=True)
    return render_template(
        "dashboard.html",
        rng=rng, span=span, kpis=kpis,
        series_json=series_json, cats_json=cats_json,
        recent=recent, goals=goals,
        lent_txt=fmt_money(bal["lent_outstanding"], cur),
        borr_txt=fmt_money(bal["borrowed_outstanding"], cur),
        overdue_n=overdue_n, due_soon_n=due_soon_n,
        reminders=rem, budgets=budgets,
        samples=int((samples or {}).get("n", 0)),
        settings=st, currency=cur, user=current_user())


@bp.get("/api/summary.json")
@login_required
def api_summary():
    """Same aggregates as the dashboard, JSON for the JS charts."""
    uid = current_user()["id"]
    import re as _re
    year = request.args.get("year", str(today_ist().year))
    if not _re.fullmatch(r"\d{4}", year or ""):
        year = str(today_ist().year)
    st = q("SELECT include_sample FROM settings WHERE user_id = ?", (uid,),
           one=True) or {"include_sample": 1}
    inc = ""
    if not st["include_sample"]:
        inc = " AND is_sample = 0"
    months = ["%02d" % (i + 1) for i in range(12)]
    series = {m: {"in": 0, "out": 0, "expense": 0, "refund": 0} for m in months}
    for r in q("SELECT substr(tx_date,1,7) AS m, direction, nature, amount_paise"
               " FROM transactions WHERE user_id = ? AND status='active'"
               " AND substr(tx_date,1,4)=?" + inc, (uid, year)):
        d = series[r["m"][5:7]]
        if r["direction"] == "in":
            d["in"] += r["amount_paise"]
        else:
            d["out"] += r["amount_paise"]
            if r["nature"] == "expense":
                d["expense"] += r["amount_paise"]
        if r["nature"] == "refund" and r["direction"] == "in":
            d["refund"] += r["amount_paise"]
    cat = q("SELECT category, TOTAL(amount_paise) AS tot FROM transactions"
            " WHERE user_id=? AND status='active' AND nature='expense'"
            " AND substr(tx_date,1,4)=?" + inc +
            " GROUP BY category ORDER BY tot DESC", (uid, year))
    bal = service.loan_balances(uid, st["include_sample"])
    cur = current_user()["currency"]
    fmt = lambda p: fmt_money(p, cur)
    return jsonify({
        "year": year,
        "months": months,
        "series": {m: {k: v / 100 for k, v in d.items()} for m, d in series.items()},
        "categories": [{"name": (r["category"] or "Other"),
                        "value": round(r["tot"] / 100, 2)} for r in cat],
        "loans": {"lent": fmt(bal["lent_outstanding"]),
                  "borrowed": fmt(bal["borrowed_outstanding"]),
                  "overdue": len(bal["overdue"])},
        "totals": {"recv": fmt(sum(d["in"] for d in series.values())),
                   "paid": fmt(sum(d["out"] for d in series.values())),
                   "expense": fmt(sum(max(0, d["expense"] - d["refund"])
                                      for d in series.values()))},
    })
