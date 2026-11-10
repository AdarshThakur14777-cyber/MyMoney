"""Authentication: login / logout, per-user g.user, CSRF tokens.
Passwords use stdlib pbkdf2_hmac (600k iterations, per-user salt).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from flask import (Blueprint, current_app, g, redirect, render_template,
                   request, session, url_for, abort)

from .db import execute, q
from .utils import now_ist_str

bp = Blueprint("auth", __name__)

_PBKDF2_ITER = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt.encode("ascii"), _PBKDF2_ITER).hex()
    return f"pbkdf2:sha256:{_PBKDF2_ITER}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        meta, salt, digest = stored.split("$", 2)      # 'pbkdf2:sha256:600000'
        iters = int(meta.split(":")[-1])
        calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                   salt.encode("ascii"), iters).hex()
        return hmac.compare_digest(calc, digest)
    except Exception:
        return False


def create_user(username: str, password: str, currency: str = "INR",
                password_repeat: str | None = None) -> int:
    """Insert user + default settings/list rows. Returns user id."""
    uid = execute(
        "INSERT INTO users (username, pw_hash, currency, created_at)"
        " VALUES (?,?,?,?)",
        (username.strip(), hash_password(password), currency, now_ist_str()))
    execute("INSERT INTO settings (user_id) VALUES (?)", (uid,))
    from .utils import CATEGORIES, PAYMENT_METHODS
    for sort, c in enumerate(CATEGORIES):
        execute("INSERT INTO list_items (user_id,list_name,value,sort)"
                " VALUES (?,?,?,?)", (uid, "category", c, sort))
    for sort, m in enumerate(PAYMENT_METHODS):
        execute("INSERT INTO list_items (user_id,list_name,value,sort)"
                " VALUES (?,?,?,?)", (uid, "payment_method", m, sort))
    return uid


def get_user_by_name(username: str):
    return q("SELECT * FROM users WHERE username = ?", (username,), one=True)


def get_user(uid: int):
    return q("SELECT * FROM users WHERE id = ?", (uid,), one=True)


def login_user(uid: int) -> None:
    session.clear()
    session["uid"] = uid
    session["csrf"] = secrets.token_hex(16)
    session.permanent = True
    execute("UPDATE users SET last_login = ? WHERE id = ?",
            (now_ist_str(), uid))


def current_user():
    if "user" not in g:
        uid = session.get("uid")
        g.user = get_user(uid) if uid else None
    return g.user


def logout_user() -> None:
    session.clear()


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


# --- blueprint: explicit auth routes ----------------------------------------
@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = get_user_by_name(username)
        if not user or not verify_password(password, user["pw_hash"]):
            return render_template(
                "login.html", error="Invalid username or password.",
                username=username), 401
        login_user(user["id"])
        nxt = request.args.get("next") or url_for("dashboard.home")
        if not nxt.startswith("/"):
            nxt = url_for("dashboard.home")
        return redirect(nxt)
    setup_needed = not q("SELECT id FROM users LIMIT 1", one=True)
    return render_template("login.html", setup_needed=setup_needed)


@bp.route("/logout", methods=["POST"])
def logout():
    verify_csrf()
    logout_user()
    return redirect(url_for("auth.login"))


def verify_csrf() -> None:
    tok_session = session.get("csrf")
    tok_form = (request.form.get("csrf_token")
                or request.headers.get("X-CSRF-Token"))
    if not tok_session or not tok_form or not hmac.compare_digest(
            tok_session, tok_form):
        abort(400, description="CSRF token missing or invalid.")


def login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("auth.login",
                                    next=request.path if request.method != "GET"
                                    else request.full_path))
        return view(*args, **kwargs)
    return wrapped


def csrf_protect() -> None:
    """Auto-check CSRF on every POST before the view runs."""
    if request.method == "POST" and current_user():
        verify_csrf()
