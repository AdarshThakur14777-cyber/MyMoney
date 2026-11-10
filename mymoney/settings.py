"""Settings page: preferences (currency, large-account threshold, sample data
visibility), category & payment-method lists, destructive data actions.
"""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import service
from .auth import (csrf_token, current_user, get_user_by_name, hash_password,
                   login_required, verify_csrf, verify_password)
from .db import execute, q, stamp_update
from .utils import compact, fmt_money, parse_money

bp = Blueprint("settings", __name__)


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    uid = current_user()["id"]
    if request.method == "POST":
        verify_csrf()
        op = request.form.get("op", "")

        if op == "prefs":
            currency = (request.form.get("currency") or "INR").strip().upper()
            if currency not in ("INR", "USD", "EUR", "GBP"):
                currency = "INR"
            alert = parse_money(request.form.get("alert_large")) or 1000000
            include_sample = 1 if request.form.get("include_sample") == "on" else 0
            execute("""UPDATE settings SET alert_large_paise = ?,
                        include_sample = ? WHERE user_id = ?""",
                    (alert, include_sample, uid))
            execute("UPDATE users SET currency = ? WHERE id = ?", (currency, uid))
            flash("Preferences saved.")

        elif op == "account":
            username = (request.form.get("username") or "").strip()
            old_password = request.form.get("current_password") or ""
            new_password = request.form.get("new_password") or ""
            confirm_password = request.form.get("confirm_password") or ""
            existing = get_user_by_name(username) if username else None
            if not verify_password(old_password, current_user()["pw_hash"]):
                flash("Current password is incorrect.")
            elif len(username) < 3 or len(username) > 40:
                flash("Username must be 3 to 40 characters.")
            elif existing and existing["id"] != uid:
                flash("That username is already in use.")
            elif new_password and len(new_password) < 8:
                flash("New password must be at least 8 characters.")
            elif new_password != confirm_password:
                flash("New passwords do not match.")
            else:
                if new_password:
                    execute("UPDATE users SET username = ?, pw_hash = ? WHERE id = ?",
                            (username, hash_password(new_password), uid))
                else:
                    execute("UPDATE users SET username = ? WHERE id = ?",
                            (username, uid))
                flash("Account details saved.")

        elif op == "add_category":
            val = compact(request.form.get("value"))
            if val:
                dup = q("SELECT id FROM list_items WHERE user_id = ? AND"
                        " list_name = 'category' AND value = ? COLLATE NOCASE",
                        (uid, val), one=True)
                if dup:
                    flash("Category already exists.")
                else:
                    execute("INSERT INTO list_items (user_id, list_name, value,"
                            " sort) VALUES (?, 'category', ?, 200)", (uid, val))
                    flash("Category added.")

        elif op == "budgets":
            categories = q("SELECT id, value FROM list_items WHERE user_id = ?"
                           " AND list_name = 'category'", (uid,))
            updates = []
            invalid = False
            for category in categories:
                raw = (request.form.get(f"budget_{category['id']}") or "").strip()
                amount = parse_money(raw) if raw else 0
                if amount is None or amount < 0 or amount > 100_000_000_000:
                    invalid = True
                    break
                updates.append((category["value"], amount))
            if invalid:
                flash("Enter valid monthly limits, or leave a field blank to remove it.")
            else:
                for category, amount in updates:
                    if amount:
                        execute("INSERT INTO category_budgets "
                                "(user_id, category, limit_paise) VALUES (?,?,?) "
                                "ON CONFLICT(user_id, category) DO UPDATE SET "
                                "limit_paise = excluded.limit_paise",
                                (uid, category, amount))
                    else:
                        execute("DELETE FROM category_budgets WHERE user_id = ?"
                                " AND category = ?", (uid, category))
                flash("Monthly budgets saved.")

        elif op == "del_category":
            val = request.form.get("value", "")
            n_used = q("SELECT COUNT(*) AS n FROM transactions WHERE user_id = ?"
                       " AND category = ?", (uid, val), one=True)
            if int((n_used or {}).get("n", 0)) > 0:
                flash("Category is in use by transactions; not removed.")
            else:
                execute("DELETE FROM list_items WHERE user_id = ? AND"
                        " list_name = 'category' AND value = ?", (uid, val))
                execute("DELETE FROM category_budgets WHERE user_id = ?"
                        " AND category = ?", (uid, val))
                flash("Category removed.")

        elif op == "del_method":
            val = request.form.get("value", "")
            execute("DELETE FROM list_items WHERE user_id = ? AND"
                    " list_name = 'payment_method' AND value = ?", (uid, val))
            flash("Payment method removed.")

        elif op == "add_method":
            val = compact(request.form.get("value"))
            if val:
                dup = q("SELECT id FROM list_items WHERE user_id = ? AND"
                        " list_name = 'payment_method' AND value = ? COLLATE NOCASE",
                        (uid, val), one=True)
                if dup:
                    flash("Payment method already exists.")
                else:
                    execute("INSERT INTO list_items (user_id, list_name, value,"
                            " sort) VALUES (?, 'payment_method', ?, 200)",
                            (uid, val))
                    flash("Payment method added.")

        elif op == "reset_sample":
            """Delete the demo story rows so the account starts clean."""
            step = request.form.get("step", "preview")
            if step != "confirm":
                return render_template(
                    "confirm_reset.html", user=current_user())
            execute("DELETE FROM attachments WHERE user_id = ?", (uid,))
            execute("DELETE FROM txn_history WHERE user_id = ?", (uid,))
            execute("DELETE FROM transactions WHERE user_id = ?", (uid,))
            execute("DELETE FROM loans WHERE user_id = ?", (uid,))
            execute("DELETE FROM people WHERE user_id = ?", (uid,))
            execute("DELETE FROM category_budgets WHERE user_id = ?", (uid,))
            flash("All your data was deleted. The account itself is untouched.")

        if request.form.get("stay"):
            return redirect(url_for("settings.settings_page"))
        return redirect(url_for("dashboard.home"))

    st = q("SELECT * FROM settings WHERE user_id = ?", (uid,), one=True)
    categories = q("SELECT * FROM list_items WHERE user_id = ? AND"
                   " list_name = 'category' ORDER BY sort, value", (uid,))
    methods = q("SELECT * FROM list_items WHERE user_id = ? AND"
                " list_name = 'payment_method' ORDER BY sort, value", (uid,))
    used = {r["category"] for r in q(
        "SELECT DISTINCT category FROM transactions WHERE user_id = ?", (uid,))}
    budget_limits = {r["category"].casefold(): r["limit_paise"]
                     for r in q("SELECT category, limit_paise FROM category_budgets"
                                " WHERE user_id = ?", (uid,))}
    return render_template(
        "settings.html", st=st or {}, categories=categories, methods=methods,
        used_categories=used, budget_limits=budget_limits,
        money_fmt=fmt_money, csrf_token=csrf_token,
        user=current_user())
