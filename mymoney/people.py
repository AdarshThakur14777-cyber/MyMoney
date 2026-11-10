"""People register: partners, merchants, lenders, borrowers."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import service
from .auth import csrf_token, current_user, login_required
from .db import execute, q, stamp_update
from .utils import fmt_money, ist_fmt_ts, now_ist_str

bp = Blueprint("people", __name__)


def _sum_rows(rows):
    tin = sum(r["in_paise"] for r in rows)
    tout = sum(r["out_paise"] for r in rows)
    return tin, tout


@bp.get("/people")
@login_required
def list_people():
    uid = current_user()["id"]
    qtext = request.args.get("q", "").strip()
    sql = """SELECT p.*,
                    COALESCE(t.in_total,0)  AS in_paise,
                    COALESCE(t.out_total,0) AS out_paise,
                    t.n_tx AS tx_count, t.last_date AS last_date
               FROM people p
          LEFT JOIN (SELECT person_id,
                            TOTAL(CASE WHEN direction = 'in'  THEN amount_paise
                                       ELSE 0 END) AS in_total,
                            TOTAL(CASE WHEN direction = 'out' THEN amount_paise
                                       ELSE 0 END) AS out_total,
                            COUNT(*) AS n_tx,
                            MAX(tx_date) AS last_date
                       FROM transactions
                      WHERE user_id = ? AND status = 'active'
                      GROUP BY person_id) t ON t.person_id = p.id
              WHERE p.user_id = ?"""
    args: tuple = (uid, uid)
    if qtext:
        sql += " AND (p.name LIKE ? OR p.phone LIKE ? OR p.upi_id LIKE ?)"
        like = "%" + qtext + "%"
        args = (uid, uid, like, like, like)
    sql += " ORDER BY p.name COLLATE NOCASE"
    rows = q(sql, args)
    cur = current_user()["currency"]
    for r in rows:
        r["received"] = fmt_money(r["in_paise"], cur)
        r["paid"] = fmt_money(r["out_paise"], cur)
        r["net"] = fmt_money(r["out_paise"] - r["in_paise"], cur)
        r["both"] = bool(r["in_paise"] and r["out_paise"])
    return render_template("people.html", people=rows, q=qtext,
                           money_fmt=fmt_money, user=current_user())


@bp.route("/people/add", methods=["GET", "POST"])
@login_required
def add_person():
    if request.method == "POST":
        uid = current_user()["id"]
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Name is required.")
        elif q("SELECT id FROM people WHERE user_id = ? AND name = ? COLLATE NOCASE",
               (uid, name), one=True):
            flash("A person with that name already exists.")
        else:
            execute("INSERT INTO people (user_id, name, phone, upi_id,"
                    " relationship, notes, created_at) VALUES (?,?,?,?,?,?,?)",
                    (uid, name, (request.form.get("phone") or "").strip() or None,
                     (request.form.get("upi_id") or "").strip() or None,
                     (request.form.get("relationship") or "").strip() or None,
                     (request.form.get("notes") or "").strip() or None,
                     now_ist_str()))
            flash("Person saved.")
            return redirect(url_for("people.list_people"))
    return render_template("person_form.html", p=None, user=current_user())


@bp.route("/person/<int:pid>/edit", methods=["GET", "POST"])
@login_required
def edit_person(pid: int):
    uid = current_user()["id"]
    p = q("SELECT * FROM people WHERE id = ? AND user_id = ?", (pid, uid),
          one=True)
    if not p:
        abort(404)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Name is required.")
        else:
            dupc = q("SELECT id FROM people WHERE user_id = ? AND name = ?"
                     " COLLATE NOCASE AND id != ?", (uid, name, pid), one=True)
            if dupc:
                flash("Another person already has that name.")
            else:
                execute("""UPDATE people SET name = ?, phone = ?, upi_id = ?,
                           relationship = ?, notes = ?, updated_at = ?
                         WHERE id = ? AND user_id = ?""",
                        (name, (request.form.get("phone") or "").strip() or None,
                         (request.form.get("upi_id") or "").strip() or None,
                         (request.form.get("relationship") or "").strip() or None,
                         (request.form.get("notes") or "").strip() or None,
                         stamp_update(), pid, uid))
                flash("Person updated.")
                return redirect(url_for("people.person_detail", pid=pid))
    return render_template("person_form.html", p=p, user=current_user())


@bp.post("/person/<int:pid>/delete")
@login_required
def delete_person(pid: int):
    from .auth import verify_csrf
    verify_csrf()
    uid = current_user()["id"]
    used = q("SELECT COUNT(*) AS n FROM transactions WHERE user_id = ? AND person_id = ?",
             (uid, pid), one=True)
    if int(used["n"] or 0) > 0:
        flash("Person has transactions and cannot be deleted (edit instead).")
    else:
        execute("DELETE FROM people WHERE id = ? AND user_id = ?", (pid, uid))
        flash("Person deleted.")
    return redirect(url_for("people.list_people"))


@bp.get("/person/<int:pid>")
@login_required
def person_detail(pid: int):
    uid = current_user()["id"]
    p = q("SELECT * FROM people WHERE id = ? AND user_id = ?", (pid, uid),
          one=True)
    if not p:
        abort(404)
    txns = q("""SELECT t.*, l.side AS loan_side
                  FROM transactions t
             LEFT JOIN loans l ON l.id = t.linked_loan_id
                 WHERE t.user_id = ? AND t.person_id = ? AND t.status = 'active'
                 ORDER BY t.tx_date DESC, t.id DESC""", (uid, pid))
    loans = [service.loan_view(uid, r, p["name"]) for r in q(
        "SELECT * FROM loans WHERE user_id = ? AND person_id = ?"
        " ORDER BY disbursed_date DESC", (uid, pid))]
    cur = current_user()["currency"]
    return render_template(
        "person_detail.html", p=p, txns=txns, loans=loans,
        money_fmt=fmt_money, currency=cur, ist_fmt_ts=ist_fmt_ts,
        natures=service.NAMES if hasattr(service, "NAMES") else
        __import__("mymoney.utils", fromlist=["NATURES"]).NATURES,
        user=current_user())
