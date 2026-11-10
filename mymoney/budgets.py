"""Current-month category budgets and spend alerts."""
from __future__ import annotations

from .db import q
from .utils import fmt_money, today_ist


def monthly_budgets(uid: int, include_sample: int, currency: str) -> list[dict]:
    """Return configured category limits with current-month net expense spend."""
    month = today_ist().strftime("%Y-%m")
    sample_clause = "" if include_sample else " AND is_sample = 0"
    spent_rows = q(
        "SELECT category, SUM(CASE WHEN nature = 'refund' THEN -amount_paise "
        "ELSE amount_paise END) AS spent FROM transactions "
        "WHERE user_id = ? AND status = 'active' "
        "AND nature IN ('expense', 'refund') AND category IS NOT NULL "
        "AND substr(tx_date, 1, 7) = ?" + sample_clause +
        " GROUP BY category COLLATE NOCASE", (uid, month))
    spent = {r["category"].casefold(): max(0, int(r["spent"] or 0))
             for r in spent_rows}
    result = []
    for row in q("SELECT category, limit_paise FROM category_budgets "
                 "WHERE user_id = ? ORDER BY category COLLATE NOCASE", (uid,)):
        limit = row["limit_paise"]
        used = spent.get(row["category"].casefold(), 0)
        pct = used * 100 // limit
        status = "over" if used >= limit else ("near" if pct >= 80 else "ok")
        result.append({"category": row["category"], "limit_paise": limit,
                       "spent_paise": used, "spent_txt": fmt_money(used, currency),
                       "limit_txt": fmt_money(limit, currency),
                       "remaining_txt": fmt_money(max(0, limit - used), currency),
                       "pct": pct, "bar_pct": min(100, pct), "status": status})
    return sorted(result, key=lambda b: (b["status"] != "over",
                                          b["status"] != "near", -b["pct"],
                                          b["category"].casefold()))
