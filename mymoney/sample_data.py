"""Fictional example records for development screenshots.

Records are marked as samples and are never loaded on application startup.
"""
from __future__ import annotations

from .db import execute
from .utils import NATURES, now_ist_str

PEOPLE = [
    ("Rahul Sharma", "+91 98200 11111", "rahul@upi", "friend"),
    ("Amit Kumar", "+91 98330 22222", "amitk@upi", "friend"),
    ("Payal Verma", "+91 98110 33333", "payalv@upi", "relative"),
    ("Ravi Furniture", "+91 22 4000 1234", "ravifurn@upi", "merchant"),
    ("SparkKart", "", "care@sparkkart.example", "merchant"),
    ("CityCabs", "", "citycabs@upi", "merchant"),
    ("FilmHouse Multiplex", "", "book@filmhouse.example", "merchant"),
    ("Priya Rawat", "+91 98450 44444", "priyar@upi", "colleague"),
]

# (date, dir, paise, person_index, nature, method, utr, category, subcat,
#  description, notes)
TXNS = [
    ("2026-06-05", "out", 26500, 0, "expense", "UPI", "UPI-441120", "Food",
     "Restaurants", "Dinner with Rahul", None),
    ("2026-06-11", "in", 4500000, None, "other_income", "Bank Transfer",
     "NEFT-778812", None, None, "Monthly salary", None),
    ("2026-06-14", "out", 120000, None, "refund", "UPI", "REF-9901", "Shopping",
     "Online Shopping", None, None),
    ("2026-06-18", "out", 85000, 1, "loan_given", "UPI", "LOAN-A123", "Financial",
     None, "Loan to Amit", "1500 due 2026-07-31"),
    ("2026-06-22", "out", 150000, 3, "expense", "Card", "", "Bills",
     "Electricity", "Electricity bill", None),
    ("2026-06-27", "in", 100000, 4, "refund", "UPI", "REF-9505", "Shopping",
     "Online Shopping", None, None),
    ("2026-07-03", "out", 22000, 5, "expense", "UPI", "UPI-101", "Transportation",
     "Taxi/Uber", None, None),
    ("2026-07-09", "out", 60000, 2, "gift_given", "UPI", "GIFT-202", "Gifts",
     "Birthday", "Payal's birthday", None),
    ("2026-07-15", "in", 50000, 4, "other_income", "UPI", "CASHB-3", None, None,
     None, "Cashback"),       # duplicate UTR story row below ("UPI-441120")
    ("2026-07-21", "out", 37500, None, "expense", "Cash", None, "Food",
     "Groceries", "Monthly groceries", None),
    ("2026-07-29", "out", 1200000, None, "expense", "Bank Transfer", "WIN-77",
     "Personal", "Electronics", "Laptop repair + parts", "Large txn"),
    ("2026-08-02", "out", 25000, 7, "expense", "UPI", "UPI-88", "Food",
     "Coffee", None, None),
    ("2026-08-08", "out", 99900, 5, "expense", "UPI", "UPI-77", "Transportation",
     "Auto/Rickshaw", None, None),
    ("2026-08-15", "in", 100000, 7, "gift_received", "Cash", None, "Gifts",
     None, "Festival gift", None),
    ("2026-08-21", "out", 450000, 4, "loan_taken", "UPI", "BORR-77", None, None,
     "Loan from SparkKart wallet", "1500 due 2026-09-01"),
    ("2026-08-28", "out", 30000, None, "expense", "", "", "Education",
     "Courses", None, "missing description demo"),
    ("2026-09-02", "in", 50000, 0, "repayment_in", "UPI", "REP-201", None, None,
     "Rahul repaid", "Part of 1500"),
    ("2026-09-06", "out", 78000, None, "expense", "UPI", "UPI-441120", "Bills",
     "Internet", None, None),
    ("2026-09-11", "out", 150000, None, "transfer", "Bank Transfer", "TRF-01",
     None, None, "Savings → Wallet", None),
    ("2026-09-18", "out", 45000, 6, "expense", "Card", "CRD-556", "Entertainment",
     "Movies", None, None),
    ("2026-09-24", "out", 62000, None, "expense", "UPI", "UPI-12", "Personal",
     "Fitness", "Gym renew", None),
    ("2026-10-01", "in", 4000000, None, "other_income", "Bank Transfer",
     "NEFT-8800", None, None, "Monthly salary", None),
    ("2026-10-08", "out", 110000, 1, "expense", "UPI", "UPI-234", "Food",
     "Restaurants", "Team dinner", "Treat by Amit"),
    ("2026-10-15", "out", 95000, 0, "repayment_out", "Cash", None,
     "Financial", None, "Repaid Rahul", None),
    ("2026-10-27", "out", 52000, None, "expense", "UPI", "UPI-99", "Shopping",
     "Clothing", "Kurta set", None),
    ("2026-11-02", "out", 25000, 5, "expense", "UPI", "UPI-321", "Transportation",
     "Metro/Bus", None, None),
    ("2026-11-05", "in", 250000, None, "other_income", "Cash", None, None, None,
     None, "Festival bonus"),
]

LOANS = [
    # (person_idx, side, paise, disbursed, due, is_samplehistoire note)
    (0, "given", 200000, "2026-06-18", "2026-07-31",
     "Give to Rahul — 2000012? amount noted"),
]


def seed_demo(uid: int) -> None:
    """Create demo people, transactions, loans and repayment links."""
    now = now_ist_str()
    pids = []
    for name, phone, upi, rel in PEOPLE:
        pids.append(execute(
            "INSERT INTO people (user_id, name, phone, upi_id, relationship,"
            " created_at) VALUES (?,?,?,?,?,?)",
            (uid, "[SAMPLE] " + name, phone, upi, rel, now)))

    txids = []
    for (d, direction, paise, pidx, nature, method, utr, cat, subcat,
         desc, notes) in TXNS:
        txids.append(execute(
            """INSERT INTO transactions (user_id, person_id, direction,
                 amount_paise, tx_date, tx_time, nature, method, utr,
                 category, subcategory, description, notes, status,
                 is_sample, created_at, updated_at)
               VALUES (?,?,?,?,?,NULL,?,?,?,?,?,?,?, 'active',1,?,?)""",
            (uid, pids[pidx] if pidx is not None else None,
             direction, paise, d, nature, method, utr, cat, subcat, desc,
             notes, now, now)))

    # UTR duplicate story: second use of UPI-441120 (index 0 vs 17) — the two
    # rows above share that UTR; the register page will flag 2 duplicates.

    # --- loans (same money story as the Excel workbook) ----------------------
    # L-01: given to Rahul 2,000.00 on 2026-06-05 via his dinner txn day? No —
    # create a clean disbursement txn, then repayments.
    disburse = [
        (0, "given", 200000, "2026-06-05", "2026-08-31",
         "[SAMPLE] Matches TXN story; partial repaid"),
        (1, "given", 150000, "2026-06-18", "2026-07-31",
         "[SAMPLE] Amit's loan; OVERDUE demo"),
        (4, "taken", 300000, "2026-08-21", "2026-12-15",
         "[SAMPLE] Borrowed from wallet; partially repaid demo"),
    ]
    loan_ids = []
    for (pidx, side, paise, dd, due, note) in disburse:
        direction = "out" if side == "given" else "in"
        nature = "loan_given" if side == "given" else "loan_taken"
        lid = execute(
            """INSERT INTO loans (user_id, person_id, side, amount_paise,
                 disbursed_date, due_date, interest_pct, notes, is_sample,
                 created_at, updated_at)
               VALUES (?,?,?,?,?,?,0,?,1,?,?)""",
            (uid, pids[pidx], side, paise, dd, due, note, now, now))
        loan_ids.append(lid)
        execute(
            """INSERT INTO transactions (user_id, person_id, direction,
                 amount_paise, tx_date, tx_time, nature, linked_loan_id,
                 method, category, description, notes, status, is_sample,
                 created_at, updated_at)
               VALUES (?,?,?,?,?,NULL,?,?,?,?,?,?, 'active',1,?,?)""",
            (uid, pids[pidx], direction, paise, dd, nature, lid, "UPI", None,
             "Loan disbursement", note, now, now))

    # repayments: 500 to Rahul (loan 1), 1,000 to wallet lender (loan 3)
    for (loan_pos, pidx, paise, d, method, note) in (
            (0, 0, 50000, "2026-09-02", "UPI", "-> 1,500 outstanding, partial"),
            (2, 4, 100000, "2026-10-15", "Cash", "-> 2,000 outstanding, partial")):
        lid = loan_ids[loan_pos]
        side = ("given" if disburse[loan_pos][1] == "given" else "taken")
        nature = "repayment_in" if side == "given" else "repayment_out"
        direction = "in" if side == "given" else "out"
        execute(
            """INSERT INTO transactions (user_id, person_id, direction,
                 amount_paise, tx_date, tx_time, nature, linked_loan_id,
                 method, utr, description, notes, status, is_sample,
                 created_at, updated_at)
               VALUES (?,?,?,?,?,NULL,?,?,?,?,?,?, 'active',1,?,?)""",
            (uid, pids[pidx], direction, paise, d, nature, lid, method, None,
             "Repayment", note, now, now))
