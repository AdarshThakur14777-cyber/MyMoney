"""Transaction routes: add/edit/cancel, attachments, audit trail.
Rules enforced:
- repayments MUST link to an existing loan (nature in/out matches loan side);
- loans are tied to a disbursement transaction; no double counting (a loan is
  one money-out/in event, repayments adjust balance only);
- duplicate warnings (same UTR / same person+date+amount) require an explicit
  'save anyway' acknowledgement.
"""
from __future__ import annotations

import os
import re
import uuid

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, send_file, url_for)

from . import service
from .auth import csrf_token, login_required, verify_csrf, current_user
from .db import execute, q, stamp_update
from .utils import (NATURES, compact, fmt_money, ist_fmt_ts, ist_fmt_date,
                    money_to_float, now_ist_str, parse_date, parse_money,
                    today_ist)

bp = Blueprint("transactions", __name__)

ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".gif", ".heic"}
MAX_ATTACHMENT = 6 * 1024 * 1024


def _need_loan(nature: str) -> bool:
    return (NATURES.get(nature) or ("", "", False))[2]


def _people(uid: int):
    return q("SELECT * FROM people WHERE user_id = ? ORDER BY name COLLATE NOCASE",
             (uid,))


def _loan_choices(uid: int, side: str | None = None):
    """Open loans for repayment linking dropdown."""
    where = " AND l.side = ?" if side else ""
    args = (current_user()["id"],) if side is None else \
           (current_user()["id"], side)
    rows = q(f"""SELECT DISTINCT l.id, l.side, l.amount_paise, l.disbursed_date,
                        l.is_sample, l.due_date,
                        p.name AS person_name
                   FROM loans l LEFT JOIN people p ON p.id = l.person_id
                  WHERE l.user_id = ?{where}
                  ORDER BY l.disbursed_date DESC""", args)
    out = []
    for r in rows:
        lv = service.loan_view(uid, r, r.get("person_name"))
        if lv["status"] != "Settled":
            out.append(lv)
    return out


def _existing_txns(uid: int):
    """Disbursement txns already recorded (for linking a loan to its txn)."""
    return q("""SELECT id, nature, tx_date, amount_paise, description
                  FROM transactions WHERE user_id = ? AND status = 'active'
                    AND nature IN ('loan_given','loan_taken')
                  ORDER BY tx_date DESC, id DESC LIMIT 200""", (uid,))


def _categories(uid: int):
    return [r["value"] for r in q(
        "SELECT value FROM list_items WHERE user_id = ? AND list_name = 'category'"
        " ORDER BY sort, value", (uid,))]


def _methods(uid: int):
    return [r["value"] for r in q(
        "SELECT value FROM list_items WHERE user_id = ? AND list_name = 'payment_method'"
        " ORDER BY sort, value", (uid,))]


def _validate(form, *, uid: int, txn_id: int | None = None) -> tuple[dict, list]:
    """Validate + normalise the txn form. Returns (data, errors)."""
    errors: list[str] = []
    data: dict = {}
    nature = form.get("nature", "")
    if nature not in NATURES:
        errors.append("Choose a transaction nature.")
    else:
        data["nature"] = nature
        data["direction"] = NATURES[nature][0]
    amount = parse_money(form.get("amount"))
    if not amount or amount <= 0:
        errors.append("Enter an amount greater than zero.")
    else:
        data["amount_paise"] = amount
    d = parse_date(form.get("tx_date") or "")
    if not d:
        errors.append("Enter a valid date (DD-MM-YYYY or YYYY-MM-DD).")
    else:
        if d > today_ist() + __import__("datetime").timedelta(days=366):
            errors.append("The date looks too far in the future.")
        data["tx_date"] = d.isoformat()
    tm = form.get("tx_time") or ""
    if tm and not re.fullmatch(r"\d{1,2}:\d{2}", tm.strip()):
        errors.append("Time must look like HH:MM.")
    else:
        data["tx_time"] = (tm.strip()[:5] if tm else None)

    person_id = form.get("person_id")
    person_id = int(person_id) if str(person_id).isdigit() else None
    new_person = compact(form.get("new_person"))
    if new_person:
        row = q("SELECT id FROM people WHERE user_id = ? AND name = ? COLLATE NOCASE",
                (uid, new_person), one=True)
        if row:
            person_id = row["id"]
        else:
            pid = execute(
                "INSERT INTO people (user_id, name, created_at) VALUES (?,?,?)",
                (uid, new_person, now_ist_str()))
            person_id = pid
    if person_id:
        owner = q("SELECT id FROM people WHERE user_id = ? AND id = ?",
                  (uid, person_id), one=True)
        if not owner:
            errors.append("Unknown person selected.")
            person_id = None
    data["person_id"] = person_id
    data["new_person"] = new_person

    data["method"] = compact(form.get("method")) or None
    data["utr"] = compact(form.get("utr")) or None
    data["category"] = compact(form.get("category")) or None
    data["subcategory"] = compact(form.get("subcategory")) or None
    data["description"] = compact(form.get("description")) or None
    data["notes"] = compact(form.get("notes")) or None

    loan_id = form.get("linked_loan_id")
    loan_id = int(loan_id) if str(loan_id).isdigit() else None
    nature = data.get("nature", "")
    if _need_loan(nature):
        if not loan_id:
            errors.append("This nature requires linking to a loan.")
        else:
            loan = service.get_loan(uid, loan_id)
            if not loan:
                errors.append("Linked loan not found.")
            elif loan["is_sample"] and not form.get("sample_ok"):
                pass                                            # allowed
            else:
                need_side = {"loan_given": None, "loan_taken": None,
                             "repayment_in": "given", "repayment_out": "taken"}
                want = need_side.get(nature)
                if nature in ("repayment_in", "repayment_out") and \
                        loan["side"] != want:
                    errors.append(
                        f"'{NATURES[nature][1]}' must link to a loan you "
                        f"{'gave' if want == 'given' else 'took'}.")
                elif nature == "loan_given" and loan["side"] != "given":
                    errors.append("Loan-link side mismatch.")
                elif nature == "loan_taken" and loan["side"] != "taken":
                    errors.append("Loan-link side mismatch.")
        data["linked_loan_id"] = loan_id
    else:
        data["linked_loan_id"] = None
    ref_of = form.get("refund_of")
    data["refund_of"] = int(ref_of) if str(ref_of).isdigit() else None
    return data, errors


@bp.route("/transactions/add", methods=["GET", "POST"])
@login_required
def add():
    uid = current_user()["id"]
    force = request.form.get("force") == "1"
    if request.method == "POST":
        data, errors = _validate(request.form, uid=uid)
        if not errors:
            dup = service.find_duplicates(
                uid, amount_paise=data["amount_paise"], tx_date=data["tx_date"],
                person_id=data["person_id"], utr=data.get("utr"))
            if dup["utr"] or dup["same_day"]:
                if not force:
                    return render_template(
                        "txn_form.html", mode="add", f=request.form,
                        errors=[], dup=dup, people=_people(uid),
                        loans=_loan_choices(uid), natures=NATURES,
                        categories=_categories(uid), methods=_methods(uid),
                        money_fmt=fmt_money, csrf_token=csrf_token,
                        user=current_user())
            txid = _insert_txn(uid, data)
            note = "Saved with duplicate warning acknowledged" if force else None
            service.add_history(uid, txid, uid, "create", None, note)
            flash("Transaction id " + str(txid) + " saved.")
            return redirect(url_for("transactions.edit", txn_id=txid))
        return render_template(
            "txn_form.html", mode="add", f=request.form, errors=errors,
            dup=None, people=_people(uid), loans=_loan_choices(uid),
            natures=NATURES, categories=_categories(uid), methods=_methods(uid),
            money_fmt=fmt_money, csrf_token=csrf_token, user=current_user())
    pref_nature = request.args.get("nature", "")
    prefill = {
        "tx_date": today_ist().isoformat(),
        "tx_time": "",
        "nature": pref_nature if pref_nature in NATURES else "expense",
        "amount": "",
        "linked_loan_id": request.args.get("loan", ""),
    }
    return render_template(
        "txn_form.html", mode="add", f=prefill, errors=[], dup=None,
        people=_people(uid), loans=_loan_choices(uid), natures=NATURES,
        categories=_categories(uid), methods=_methods(uid),
        money_fmt=fmt_money, csrf_token=csrf_token, user=current_user())


def _insert_txn(uid: int, data: dict) -> int:
    now = now_ist_str()
    # Only the bundled starter story is sample data; personal entries are real.
    is_sample = 0
    return execute(
        """INSERT INTO transactions (user_id, person_id, direction,
             amount_paise, tx_date, tx_time, nature, linked_loan_id, refund_of,
             method, utr, category, subcategory, description, notes,
             status, is_sample, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'active', ?, ?, ?)""",
        (uid, data["person_id"], data["direction"], data["amount_paise"],
         data["tx_date"], data.get("tx_time"), data["nature"],
         data.get("linked_loan_id"), data.get("refund_of"),
         data.get("method"), data.get("utr"), data.get("category"),
         data.get("subcategory"), data.get("description"), data.get("notes"),
         is_sample, now, now))


EDITABLE = ["person_id", "amount_paise", "tx_date", "tx_time", "nature",
            "linked_loan_id", "refund_of", "method", "utr", "category",
            "subcategory", "description", "notes", "status"]


@bp.route("/txn/<int:txn_id>/edit", methods=["GET", "POST"])
@login_required
def edit(txn_id: int):
    uid = current_user()["id"]
    txn = service.get_txn(uid, txn_id)
    if not txn:
        abort(404)
    if request.method == "POST":
        data, errors = _validate(request.form, uid=uid)
        if not errors:
            dup = service.find_duplicates(
                uid, amount_paise=data["amount_paise"], tx_date=data["tx_date"],
                person_id=data["person_id"], utr=data.get("utr"),
                exclude_id=txn_id)
            if (dup["utr"] or dup["same_day"]) and \
                    request.form.get("force") != "1":
                f = dict(request.form)
                f.setdefault("amount", money_to_float(data["amount_paise"]))
                return render_template(
                    "txn_form.html", mode="edit", f=f, errors=[],
                    txn=txn, dup=dup, people=_people(uid),
                    loans=_loan_choices(uid), natures=NATURES,
                    categories=_categories(uid), methods=_methods(uid),
                    money_fmt=fmt_money, csrf_token=csrf_token,
                    user=current_user(), attachments=service.attachments_for(
                        uid, txn_id))
            changes = {}
            for k in EDITABLE:
                old = txn.get(k)
                new = data.get(k)
                if k == "status":
                    new = "cancelled" if request.form.get("cancel") else txn["status"]
                if old != new:
                    changes[k] = {"old": old, "new": new}
            if changes:
                sets = ", ".join(f"{k} = ?" for k in changes)
                args = [changes[k]["new"] for k in changes] + [stamp_update(), txn_id, uid]
                execute(f"UPDATE transactions SET {sets}, updated_at = ?"
                        f" WHERE id = ? AND user_id = ?", tuple(args))
                service.add_history(uid, txn_id, uid, "edit", changes)
            flash("Transaction updated.")
            return redirect(url_for("transactions.edit", txn_id=txn_id))
        f = dict(request.form)
        f.setdefault("amount", money_to_float(txn["amount_paise"]))
        return render_template(
            "txn_form.html", mode="edit", f=f, errors=errors, txn=txn,
            dup=None, people=_people(uid), loans=_loan_choices(uid),
            natures=NATURES, categories=_categories(uid), methods=_methods(uid),
            money_fmt=fmt_money, csrf_token=csrf_token, user=current_user(),
            attachments=service.attachments_for(uid, txn_id))
    f = dict(txn)
    f["amount"] = money_to_float(txn["amount_paise"])
    f["tx_date"] = txn["tx_date"]
    return render_template(
        "txn_form.html", mode="edit", f=f, errors=None, txn=txn, dup=None,
        people=_people(uid), loans=_loan_choices(uid), natures=NATURES,
        categories=_categories(uid), methods=_methods(uid),
        money_fmt=fmt_money, csrf_token=csrf_token, user=current_user(),
        attachments=service.attachments_for(uid, txn_id))


@bp.post("/txn/<int:txn_id>/cancel")
@login_required
def cancel(txn_id: int):
    verify_csrf()
    uid = current_user()["id"]
    txn = service.get_txn(uid, txn_id)
    if not txn:
        abort(404)
    execute("UPDATE transactions SET status = 'cancelled', updated_at = ?"
            " WHERE id = ? AND user_id = ?", (stamp_update(), txn_id, uid))
    service.add_history(uid, txn_id, uid, "cancel",
                        {"status": {"old": "active", "new": "cancelled"}})
    flash("Transaction cancelled (kept in history).")
    return redirect(url_for("transactions.list", **{"keep": 1}))


@bp.post("/txn/<int:txn_id>/restore")
@login_required
def restore(txn_id: int):
    verify_csrf()
    uid = current_user()["id"]
    txn = service.get_txn(uid, txn_id)
    if not txn:
        abort(404)
    execute("UPDATE transactions SET status = 'active', updated_at = ?"
            " WHERE id = ? AND user_id = ?", (stamp_update(), txn_id, uid))
    service.add_history(uid, txn_id, uid, "restore",
                        {"status": {"old": "cancelled", "new": "active"}})
    flash("Transaction restored.")
    return redirect(url_for("transactions.txn_detail", txn_id=txn_id))


@bp.get("/transactions")
@login_required
def list():
    uid = current_user()["id"]
    st = q("SELECT include_sample FROM settings WHERE user_id = ?", (uid,),
           one=True) or {"include_sample": 1}
    f = {k: request.args.get(k, "") for k in
         ("month", "year", "direction", "nature", "person_id", "category",
          "q", "sample")}
    rows = service.list_txns(uid, f, st["include_sample"])
    years = [r["y"] for r in q(
        "SELECT DISTINCT substr(tx_date,1,4) AS y FROM transactions"
        " WHERE user_id = ? ORDER BY y DESC", (uid,))]
    from .utils import today_ist
    year_now = str(today_ist().year)
    if year_now not in years:
        years.insert(0, year_now)
    people = _people(uid)
    categories = _categories(uid)
    total_in = sum(r["amount_paise"] for r in rows if r["direction"] == "in")
    total_out = sum(r["amount_paise"] for r in rows if r["direction"] == "out")
    net_total = total_in - total_out
    cur = current_user()["currency"]
    return render_template(
        "transactions.html", txns=rows, f=f, years=years, year_now=year_now,
        people=people, categories=categories, natures=NATURES,
        methods=_methods(uid),
        total_in=fmt_money(total_in, cur), total_out=fmt_money(total_out, cur),
        net_total=net_total,
        money_fmt=fmt_money, currency=cur, settings=st,
        samples=sum(1 for r in rows if r["is_sample"]),
        user=current_user())


@bp.get("/txn/<int:txn_id>")
@login_required
def txn_detail(txn_id: int):
    uid = current_user()["id"]
    txn = service.get_txn(uid, txn_id)
    if not txn:
        abort(404)
    uploads_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return render_template(
        "txn_detail.html", t=txn, natures=NATURES,
        attachments=service.attachments_for(uid, txn_id),
        history=service.txn_history(uid, txn_id),
        money_fmt=fmt_money, currency=current_user()["currency"],
        ist_fmt_ts=ist_fmt_ts, ist_fmt_date=ist_fmt_date,
        csrf_token=csrf_token, user=current_user())


# --- attachments ------------------------------------------------------------
@bp.post("/txn/<int:txn_id>/attach")
@login_required
def attach(txn_id: int):
    verify_csrf()
    uid = current_user()["id"]
    txn = service.get_txn(uid, txn_id)
    if not txn:
        abort(404)
    up = request.files.get("file")
    if not up or not up.filename:
        flash("Choose a file to attach.")
        return redirect(url_for("transactions.edit", txn_id=txn_id))
    ext = os.path.splitext(up.filename.lower())[1]
    if ext not in ALLOWED_EXT:
        flash("Attachment type not allowed (png/jpg/webp/pdf).")
        return redirect(url_for("transactions.edit", txn_id=txn_id))
    blob = up.read()
    if len(blob) > MAX_ATTACHMENT:
        flash("Attachment too large (max 6 MB).")
        return redirect(url_for("transactions.edit", txn_id=txn_id))
    uploads = current_app.config["UPLOAD_DIR"]
    stored = f"{uid}_{uuid.uuid4().hex}{ext}"
    os.makedirs(os.path.join(uploads, str(uid)), exist_ok=True)
    path = os.path.join(uploads, str(uid), stored)
    with open(path, "wb") as fh:
        fh.write(blob)
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "gif": "image/gif", "pdf": "application/pdf",
            "heic": "image/heic"}.get(ext.lstrip("."), "application/octet-stream")
    execute("INSERT INTO attachments (user_id, txn_id, orig_name, stored_name,"
            " mime, size_bytes, uploaded_at) VALUES (?,?,?,?,?,?,?)",
            (uid, txn_id, compact(up.filename)[:120], stored, mime, len(blob),
             now_ist_str()))
    service.add_history(uid, txn_id, uid, "attachment",
                        {"file": {"new": compact(up.filename)[:120]}})
    flash("Receipt attached.")
    return redirect(url_for("transactions.edit", txn_id=txn_id))


@bp.get("/attach/<int:att_id>")
@login_required
def attach_download(att_id: int):
    uid = current_user()["id"]
    row = q("SELECT * FROM attachments WHERE id = ? AND user_id = ?",
            (att_id, uid), one=True)
    if not row:
        abort(404)
    path = os.path.join(current_app.config["UPLOAD_DIR"], str(uid),
                        row["stored_name"])
    if not os.path.exists(path):
        abort(404)
    from datetime import datetime
    response = send_file(path, mimetype=row["mime"], as_attachment=False)
    response.headers["Content-Disposition"] = (
        "inline; filename*=UTF-8''%s" % _rfc5987(row["orig_name"]))
    return response


def _rfc5987(name: str) -> str:
    from urllib.parse import quote
    return quote(name, safe="")


@bp.post("/attach/<int:att_id>/delete")
@login_required
def attach_delete(att_id: int):
    verify_csrf()
    uid = current_user()["id"]
    row = q("SELECT * FROM attachments WHERE id = ? AND user_id = ?",
            (att_id, uid), one=True)
    back = request.form.get("back") or ""
    if not back.startswith("/"):
        back = url_for("dashboard.home")
    if row:
        path = os.path.join(current_app.config["UPLOAD_DIR"], str(uid),
                            row["stored_name"])
        if os.path.exists(path):
            os.remove(path)
        execute("DELETE FROM attachments WHERE id = ? AND user_id = ?",
                (att_id, uid))
        flash("Attachment removed.")
    return redirect(back)
