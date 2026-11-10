"""Loans (given & taken), repayment log, explicit linking, reminders view."""
from __future__ import annotations

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)

from . import service
from .auth import csrf_token, current_user, login_required, verify_csrf
from .db import execute, q, stamp_update
from .utils import (fmt_money, ist_fmt_date, ist_fmt_ts, now_ist_str, parse_date,
                    parse_money, today_ist)

bp = Blueprint("loans", __name__)


def _people(uid):
    return q("SELECT id, name FROM people WHERE user_id = ?"
             " ORDER BY name COLLATE NOCASE", (uid,))


@bp.get("/loans")
@login_required
def loans():
    uid = current_user()["id"]
    st = q("SELECT include_sample FROM settings WHERE user_id = ?", (uid,),
           one=True) or {"include_sample": 1}
    lo = service.all_loans(uid, st["include_sample"])
    given = [d for d in lo if d["side"] == "given"]
    taken = [d for d in lo if d["side"] == "taken"]
    cur = current_user()["currency"]
    replog = q("""SELECT t.*, p.name AS person_name, l.side AS loan_side,
                         l.amount_paise AS loan_amount, lp.name AS loan_person
                    FROM transactions t
               LEFT JOIN loans l ON l.id = t.linked_loan_id
               LEFT JOIN people p ON p.id = t.person_id
               LEFT JOIN people lp ON lp.id = l.person_id
                   WHERE t.user_id = ? AND t.status = 'active'
                     AND t.nature IN ('repayment_in','repayment_out')
                     AND (? = 1 OR t.is_sample = 0)
                   ORDER BY t.tx_date DESC, t.id DESC LIMIT 200""", (uid, st["include_sample"]))
    return render_template(
        "loans.html", given=given, taken=taken, replog=replog,
        reminders=service.reminders(uid, st["include_sample"]),
        money_fmt=fmt_money, currency=cur, ist_fmt_date=ist_fmt_date,
        today=today_ist().isoformat(), people=_people(uid),
        csrf_token=csrf_token, user=current_user())


@bp.route("/loans/add", methods=["GET", "POST"])
@login_required
def add_loan():
    uid = current_user()["id"]
    if request.method == "POST":
        side = request.form.get("side", "")
        amount = parse_money(request.form.get("amount"))
        dd = parse_date(request.form.get("disbursed_date") or "")
        due = parse_date(request.form.get("due_date") or "")
        person_id = request.form.get("person_id", "")
        person_id = int(person_id) if person_id.isdigit() else None
        first_txn_id = request.form.get("first_txn_id", "")
        first_txn_id = int(first_txn_id) if first_txn_id.isdigit() else None
        ft = None
        notes = (request.form.get("notes") or "").strip() or None
        errors = []
        if side not in ("given", "taken"):
            errors.append("Choose whether money was given or taken.")
        if not amount or amount <= 0:
            errors.append("Principal must be greater than zero.")
        if not dd:
            errors.append("A disbursement date is required.")
        elif (due and dd and due < dd):
            errors.append("Due date cannot be before disbursement.")
        if not person_id:
            errors.append("Pick the person involved (add them first if new).")
        if first_txn_id:
            ft = q("SELECT * FROM transactions WHERE id = ? AND user_id = ?",
                   (first_txn_id, uid), one=True)
            if not ft:
                errors.append("Disbursement transaction not found.")
        if errors:
            for e in errors:
                flash(e)
            return render_template(
                "loan_form.html", loan=request.form, people=_people(uid),
                existing=_existing_disb(uid), user=current_user())
        is_sample = 1 if (ft and ft["is_sample"]) else 0
        try:
            interest = float(request.form.get("interest_pct") or 0)
        except (TypeError, ValueError):
            interest = 0.0
        if not (0 <= interest <= 100):
            interest = 0.0
        lid = execute(
            """INSERT INTO loans (user_id, person_id, side, amount_paise,
                 disbursed_date, due_date, interest_pct, notes, is_sample,
                 created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, person_id, side, amount, dd.isoformat(),
             due.isoformat() if due else None, interest, notes, is_sample,
             now_ist_str(), now_ist_str()))
        auto_txn = (request.form.get("record_disbursement") or "") == "on"
        if auto_txn and not first_txn_id:
            execute(
                """INSERT INTO transactions
                     (user_id, person_id, direction, amount_paise, tx_date,
                      tx_time, nature, linked_loan_id, method, utr, category,
                      subcategory, description, notes, status, is_sample,
                      created_at, updated_at)
                   VALUES (?,?,?,?,?,NULL,?,?,?,?,NULL,NULL,?,?,
                           'active',?,?,?)""",
                (uid, person_id, "out" if side == "given" else "in",
                 amount, dd.isoformat(),
                 "loan_given" if side == "given" else "loan_taken", lid,
                 (request.form.get("disb_method") or "").strip() or None,
                 "Loan disbursement", notes, is_sample,
                 now_ist_str(), now_ist_str()))
            service.add_history(uid, lid, uid, "create", None,
                                "Loan created with disbursement transaction")
        elif first_txn_id:
            execute("UPDATE transactions SET linked_loan_id = ?, updated_at = ?"
                    " WHERE id = ? AND user_id = ?",
                    (lid, stamp_update(), first_txn_id, uid))
            service.add_history(uid, first_txn_id, uid, "edit",
                                {"linked_loan_id": {"old": None, "new": lid}},
                                "Linked disbursement transaction to loan")
        flash("Loan saved.")
        return redirect(url_for("loans.loan_detail", lid=lid))
    return render_template("loan_form.html", loan=None, people=_people(uid),
                           existing=_existing_disb(uid), user=current_user())


def _existing_disb(uid):
    return q("""SELECT id, nature, tx_date, amount_paise FROM transactions
                 WHERE user_id = ? AND status = 'active'
                   AND nature IN ('loan_given','loan_taken')
                   AND linked_loan_id IS NULL
                 ORDER BY tx_date DESC LIMIT 50""", (uid,))


@bp.get("/loan/<int:lid>")
@login_required
def loan_detail(lid: int):
    uid = current_user()["id"]
    loan = service.get_loan(uid, lid)
    if not loan:
        abort(404)
    txns = q("""SELECT t.*, p.name AS person_name
                  FROM transactions t
             LEFT JOIN people p ON p.id = t.person_id
                 WHERE t.user_id = ? AND t.linked_loan_id = ?
                   AND t.status = 'active'
                 ORDER BY t.tx_date DESC, t.id DESC""", (uid, lid))
    cur = current_user()["currency"]
    return render_template(
        "loan_detail.html", loan=loan, txns=txns,
        side_label=("You gave" if loan["side"] == "given" else "You took"),
        expected_side=("given" if loan["side"] == "given" else "taken"),
        repayment_nature=("repayment_in" if loan["side"] == "given"
                          else "repayment_out"),
        repayment_label=("Repayment received" if loan["side"] == "given"
                         else "Repayment made"),
        money_fmt=fmt_money, currency=cur, ist_fmt_date=ist_fmt_date,
        ist_fmt_ts=ist_fmt_ts, people=_people(uid),
        csrf_token=csrf_token, user=current_user())


@bp.route("/loan/<int:lid>/repay", methods=["POST"])
@login_required
def repay(lid: int):
    verify_csrf()
    uid = current_user()["id"]
    loan = service.get_loan(uid, lid)
    if not loan:
        abort(404)
    amount = parse_money(request.form.get("amount"))
    dd = parse_date(request.form.get("tx_date") or "")
    method = (request.form.get("method") or "").strip() or None
    utr = (request.form.get("utr") or "").strip() or None
    notes = (request.form.get("notes") or "").strip() or None
    confirm_link = request.form.get("confirm_link") == "on"
    errors = []
    if not amount or amount <= 0:
        errors.append("Enter a repayment amount greater than zero.")
    if not dd:
        errors.append("Enter the repayment date.")
    if not confirm_link:
        errors.append("Please confirm this repayment is linked to this loan.")
    if amount and amount > loan["balance_paise"]:
        errors.append("Amount exceeds the outstanding balance (%s). Repayments"
                      " never exceed the principal." % fmt_money(
                          loan["balance_paise"], current_user()["currency"]))
    if errors:
        for e in errors:
            flash(e)
    else:
        now = now_ist_str()
        nature = "repayment_in" if loan["side"] == "given" else "repayment_out"
        direction = "in" if loan["side"] == "given" else "out"
        txid = execute(
            """INSERT INTO transactions (user_id, person_id, direction,
                 amount_paise, tx_date, tx_time, nature, linked_loan_id,
                 method, utr, description, notes, status, is_sample,
                 created_at, updated_at)
               VALUES (?,?,?,?,?,NULL,?,?,?,?,?,?, 'active',?,?,?)""",
            (uid, loan["person_id"], direction, amount, dd.isoformat(), nature,
             lid, method, utr,
             "Repayment on loan " + str(lid), notes, loan["is_sample"], now, now))
        service.add_history(uid, txid, uid, "create", None,
                            "Repayment linked to loan " + str(lid))
        new_bal = service.loan_repayment_total(uid, lid, loan["is_sample"])
        status = "Settled" if new_bal >= loan["amount_paise"] else "Partially Repaid"
        execute("UPDATE loans SET updated_at = ? WHERE id = ? AND user_id = ?",
                (stamp_update(), lid, uid))
        flash("Repayment saved (%s). Balance now ₹%s — status %s." % (
            nature, fmt_money(max(0, loan["amount_paise"] - new_bal),
                              current_user()["currency"]), status))
    return redirect(url_for("loans.loan_detail", lid=lid))


@bp.post("/loan/<int:lid>/delete")
@login_required
def delete_loan(lid: int):
    verify_csrf()
    uid = current_user()["id"]
    attached = q("SELECT COUNT(*) AS n FROM transactions WHERE user_id = ?"
                 " AND linked_loan_id = ? AND status = 'active'",
                 (uid, lid), one=True)
    if int(attached["n"] or 0) > 0:
        flash("This loan has transactions linked to it and cannot be deleted.")
    else:
        execute("DELETE FROM loans WHERE id = ? AND user_id = ?", (lid, uid))
        flash("Loan deleted.")
    return redirect(url_for("loans.loans"))


@bp.get("/reminders")
@login_required
def reminders():
    uid = current_user()["id"]
    st = q("SELECT include_sample FROM settings WHERE user_id = ?", (uid,),
           one=True) or {"include_sample": 1}
    r = service.reminders(uid, st["include_sample"])
    cur = current_user()["currency"]
    return render_template("reminders.html", r=r, money_fmt=fmt_money,
                           currency=cur, ist_fmt_date=ist_fmt_date,
                           today=today_ist().isoformat(), user=current_user())
