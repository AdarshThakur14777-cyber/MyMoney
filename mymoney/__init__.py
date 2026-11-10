"""create_app factory, template globals, request lifecycle."""
from __future__ import annotations

import os
import secrets

from flask import Flask, g, render_template, request

from . import auth as auth_bp_mod
from . import service
from .auth import csrf_protect, current_user, login_required
from .db import close_db, init_db, q
from .utils import (CATEGORIES, SUBCATS, NATURES, fmt_money, fmt_money_short,
                    ist_fmt_date, ist_fmt_ts)
import json as _json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
UPLOAD_DIR = os.path.join(INSTANCE_DIR, "uploads")


def create_app(config: dict | None = None) -> Flask:
    app = Flask("mymoney",
                template_folder=os.path.join(BASE_DIR, "templates"),
                static_folder=os.path.join(BASE_DIR, "static"),
                instance_path=INSTANCE_DIR)
    for key, default in (  # security defaults
        ("SECRET_KEY", None),
        ("SESSION_COOKIE_HTTPONLY", True),
        ("SESSION_COOKIE_SAMESITE", "Lax"),
        ("MAX_CONTENT_LENGTH", 8 * 1024 * 1024),
    ):
        if key == "SECRET_KEY":
            continue
        app.config[key] = default
    app.config["SECRET_KEY"] = os.environ.get(
        "MYMONEY_SECRET") or _read_or_create_secret()
    app.config["UPLOAD_DIR"] = UPLOAD_DIR
    app.config.update(config or {})

    init_db()

    from .auth import bp as authbp
    from .dashboard import bp as dashbp
    from .loans import bp as loansbp
    from .people import bp as peoplebp
    from .reports import bp as reportsbp
    from .settings import bp as settingsbp
    from .transactions import bp as txbp
    app.register_blueprint(authbp)
    app.register_blueprint(dashbp)
    app.register_blueprint(txbp)
    app.register_blueprint(peoplebp)
    app.register_blueprint(loansbp)
    app.register_blueprint(reportsbp)
    app.register_blueprint(settingsbp)

    app.teardown_appcontext(close_db)

    @app.before_request
    def hooks():
        # Login has no existing session; all authenticated writes need CSRF.
        if request.method == "POST" and request.path != "/login":
            csrf_protect()

    # template globals -------------------------------------------------------
    app.jinja_env.filters["fromjson"] = lambda s: _json.loads(s or "{}")

    import datetime as _dtmod

    def _ist_hour() -> int:
        ist = _dtmod.timezone(_dtmod.timedelta(hours=5, minutes=30))
        return _dtmod.datetime.now(ist).hour

    def _nav_counts() -> dict:
        """Tiny per-request stats for the sidebar/bell badges."""
        u = current_user()
        if not u or not u["id"]:
            return {"txns": 0, "rem": 0}
        uid = u["id"]
        st = q("SELECT include_sample FROM settings WHERE user_id = ?",
               (uid,), one=True) or {"include_sample": 1}
        inc = "" if st["include_sample"] else " AND is_sample = 0"
        row = q("SELECT COUNT(*) AS n FROM transactions WHERE user_id = ?"
                " AND status = 'active'" + inc, (uid,), one=True)
        rem = service.reminders(uid, st["include_sample"])
        return {"txns": int(row["n"]),
                "rem": len(rem["overdue"]) + len(rem["due_soon"])
                       + len(rem["budget_alerts"])}

    app.jinja_env.globals.update({
        "NATURES": NATURES,
        "CATEGORIES": CATEGORIES,
        "SUBCATS": SUBCATS,
        "NATURE_ICONS": {
            "expense":       "🛒",
            "refund":        "↩️",
            "other_income":  "💵",
            "loan_given":    "🤝",
            "loan_taken":    "🏦",
            "repayment_in":  "📥",
            "repayment_out": "📤",
            "gift_given":    "🎁",
            "gift_received": "🎉",
            "transfer":      "🔁",
        },
        "fmt_money": fmt_money,
        "fmt_money_short": fmt_money_short,
        "money_fmt": fmt_money,
        "ist_fmt_date": ist_fmt_date,
        "ist_fmt_ts": ist_fmt_ts,
        "ist_hour": _ist_hour,
        "nav_counts": _nav_counts,
        "csrf_token": auth_bp_mod.csrf_token,
        "current_user": current_user,
        "nav_items": [
            ("dashboard.home", "Dashboard", "dashboard"),
            ("transactions.list", "Transactions", "arrow"),
            ("people.list_people", "People", "contacts"),
            ("loans.loans", "Loans", "loans"),
            ("reports.reports_page", "Reports", "docs"),
            ("settings.settings_page", "Settings", "gear"),
        ],
    })

    @app.errorhandler(404)
    def nf(e):
        return render_template("error.html", code=404,
                               msg="Page not found."), 404

    @app.errorhandler(400)
    def bad(e):
        return render_template("error.html", code=400,
                               msg=getattr(e, "description",
                                           "Bad request.")), 400

    return app


def _secret_file() -> str:
    return os.path.join(INSTANCE_DIR, "secret_key")


def _read_or_create_secret() -> str:
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    path = _secret_file()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            val = fh.read().strip()
            if val:
                return val
    val = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(val)
    return val
