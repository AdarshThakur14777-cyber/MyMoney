"""Shared helpers: IST timezone, integer-paise money, Indian formatting."""
from __future__ import annotations

import datetime as _dt
import re as _re
from decimal import Decimal, InvalidOperation

# --- Asia/Kolkata is a fixed offset; no DST, so we model it directly ---------
IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30), name="Asia/Kolkata")


def now_ist() -> _dt.datetime:
    return _dt.datetime.now(tz=IST)


def now_ist_str() -> str:
    """Canonical stored timestamp: 'YYYY-MM-DD HH:MM:SS (IST)'."""
    return now_ist().strftime("%Y-%m-%d %H:%M:%S")


def today_ist() -> _dt.date:
    return now_ist().date()


def parse_date(s: str):
    """Accepts YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY -> date, or None."""
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def ist_fmt_date(iso: str) -> str:
    """Stored '2026-11-10' -> display '10-11-2026'."""
    d = parse_date(iso or "")
    return d.strftime("%d-%m-%Y") if d else (iso or "")


def ist_fmt_time(hhmm: str) -> str:
    return (hhmm or "")[:5]


def ist_fmt_ts(ts: str) -> str:
    """Stored '2026-11-10 09:30:12' -> '10-11-2026 09:30'."""
    try:
        return _dt.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime(
            "%d-%m-%Y %H:%M")
    except Exception:
        return ts or ""


# --- Money as integer paise -------------------------------------------------
CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}


def parse_money(text) -> int | None:
    """'1,234.50' / '₹ 1234' / '1234' -> paise int. None if unparseable."""
    if text is None:
        return None
    s = str(text)
    for sym in ("₹", "$", "€", "£", ",", " "):
        s = s.replace(sym, "")
    s = s.strip()
    if not s:
        return None
    try:
        return int((Decimal(s) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


def money_to_float(paise: int | None) -> float:
    return round((paise or 0) / 100.0, 2)


def _in_groups(intpart: str) -> str:
    """Indian digit grouping: 1234567 -> 12,34,567."""
    if len(intpart) <= 3:
        return intpart
    head, tail = intpart[:-3], intpart[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def fmt_money(paise, currency: str = "INR") -> str:
    """125000 paise -> '₹1,250.00'. Rounded half-up on display."""
    p = int(paise or 0)
    neg = p < 0
    p = abs(p)
    rupees = p // 100
    rem = p % 100
    out = f"{CURRENCY_SYMBOLS.get(currency, currency)}{_in_groups(str(rupees))}.{rem:02d}"
    return f"-{out}" if neg else out


def fmt_money_short(paise, currency: str = "INR") -> str:
    """125000 -> '₹1,250'. Used on pill/charts to save space."""
    s = fmt_money(int(paise or 0), currency)
    if s.endswith(d := ".00"):
        return s[: -len(d)]
    return s


# --- misc -------------------------------------------------------------------
NONSPACE = _re.compile(r"\s+")


def compact(s) -> str:
    return NONSPACE.sub(" ", str(s or "")).strip()


def month_of_date(iso: str) -> str:
    return (iso or "")[:7]            # 'YYYY-MM'


CATEGORIES = [
    "Food", "Shopping", "Transportation", "Education", "Entertainment",
    "Bills", "Personal", "Financial", "Gifts", "Other",
]

SUBCATS = {
    "Food": ["Groceries", "Restaurants", "Food Delivery", "Coffee"],
    "Shopping": ["Clothing", "Electronics", "Online Shopping", "Household"],
    "Transportation": ["Fuel", "Auto/Rickshaw", "Taxi/Uber", "Metro/Bus", "Parking"],
    "Education": ["Tuition", "Books", "Courses", "Fees"],
    "Entertainment": ["Movies", "Streaming", "Games", "Events"],
    "Bills": ["Electricity", "Mobile", "Internet", "Water", "Rent", "Gas"],
    "Personal": ["Healthcare", "Fitness", "Salon", "Grooming", "Medicines"],
    "Financial": ["Insurance", "Investments", "SIP", "Loan EMI", "Charges"],
    "Gifts": ["Birthday", "Festival", "Wedding", "Donation"],
    "Other": ["Misc"],
}

PAYMENT_METHODS = ["UPI", "Cash", "Bank Transfer", "IMPS", "NEFT", "Card",
                   "Cheque", "Wallet", "Other"]

NATURES = {
    # nature: (direction, display label, needs loan link?)
    "expense":        ("out", "Expense", False),
    "refund":         ("in", "Refund Received", False),
    "other_income":   ("in", "Other Income", False),
    "loan_given":     ("out", "Loan Given", True),
    "loan_taken":     ("in", "Loan Taken", True),
    "repayment_in":   ("in", "Repayment Received", True),
    "repayment_out":  ("out", "Repayment Made", True),
    "gift_given":     ("out", "Gift Given", False),
    "gift_received":  ("in", "Gift Received", False),
    "transfer":       ("out", "Self Transfer Out", False),
}

# plain "expense-like" natures that count as actual spending / inflow
EXPENSE_NATURES = ("expense",)
INFLOW_NATURES = ("other_income", "refund", "gift_received")
