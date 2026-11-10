"""Report downloads: CSV, Excel (openpyxl), PDF (reportlab) and print view."""
from __future__ import annotations

import csv
import io
import os

from flask import Blueprint, Response, current_app, render_template, request

from . import service
from .auth import current_user, login_required
from .db import q
from .utils import fmt_money, ist_fmt_date, now_ist_str

bp = Blueprint("reports", __name__)

COLS = ["Date", "Time", "Direction", "Nature", "Person", "Category",
        "Subcategory", "Description", "Method", "UTR", "Linked Loan",
        "Amount"]


def _rows_for(uid) -> tuple[list[dict], str]:
    st = q("SELECT include_sample FROM settings WHERE user_id = ?", (uid,),
           one=True) or {"include_sample": 1}
    f = {k: request.args.get(k, "") for k in
         ("month", "year", "direction", "nature", "person_id", "category", "q")}
    rows = service.list_txns(uid, f, st["include_sample"], limit=5000)
    title_bits = []
    if f.get("month") and f["month"] != "all":
        title_bits.append("Month " + f["month"])
    if f.get("year") and f["year"] != "all":
        title_bits.append("Year " + f["year"])
    return rows, (" for ".join(title_bits) if title_bits else "all time")


def _hydrate(rows, currency):
    out = []
    for r in rows:
        lp = r.get("loan_person")
        out.append({
            "Date": ist_fmt_date(r["tx_date"]),
            "Time": (r.get("tx_time") or ""),
            "Direction": "In" if r["direction"] == "in" else "Out",
            "Nature": r["nature"].replace("_", " ").title(),
            "Person": r.get("person_name") or "",
            "Category": r.get("category") or "",
            "Subcategory": r.get("subcategory") or "",
            "Description": r.get("description") or "",
            "Method": r.get("method") or "",
            "UTR": r.get("utr") or "",
            "Linked Loan": (("loan " + str(r["linked_loan_id"])) if
                            r.get("linked_loan_id") else ""),
            "Amount": r["amount_paise"],
            "_sample": bool(r.get("is_sample")),
        })
    return out


def _totals(rows):
    tin = sum(r["amount_paise"] for r in rows if r["direction"] == "in")
    tout = sum(r["amount_paise"] for r in rows if r["direction"] == "out")
    return tin, tout - tin, tout


@bp.get("/reports")
@login_required
def reports_page():
    uid = current_user()["id"]
    st = q("SELECT include_sample FROM settings WHERE user_id = ?", (uid,),
           one=True) or {"include_sample": 1}
    rows, scope = _rows_for(uid)
    cur = current_user()["currency"]
    tin, net, tout = _totals(rows)
    cat = {}
    for r in rows:
        if r["nature"] == "expense":
            cat[r.get("category") or "Other"] = cat.get(
                r.get("category") or "Other", 0) + r["amount_paise"]
    cat_bars = []
    cat_max = max(cat.values()) if cat else 0
    for name, val in sorted(cat.items(), key=lambda kv: -kv[1])[:12]:
        cat_bars.append({
            "name": name,
            "fmt": fmt_money(val, cur),
            "pct": int(val * 100 / cat_max) if cat_max else 0,
        })
    years = [r["y"] for r in q(
        "SELECT DISTINCT substr(tx_date,1,4) AS y FROM transactions"
        " WHERE user_id = ? ORDER BY y DESC", (uid,))]
    from .utils import today_ist
    ynow = str(today_ist().year)
    if ynow not in years:
        years.insert(0, ynow)
    data = _hydrate(rows, cur)
    return render_template(
        "reports.html", rows=data[:300], rows_all=data, scope=scope,
        f={k: request.args.get(k, "") for k in
           ("month", "year", "direction", "nature", "person_id", "category", "q")},
        total_in=fmt_money(tin, cur), total_out=fmt_money(tout, cur),
        net=fmt_money(net, cur), n=len(data),
        cat_bars=cat_bars, years=years, ynow=ynow,
        money_fmt=fmt_money, currency=cur, user=current_user())


@bp.route("/reports.json", methods=["POST", "GET"])
@login_required
def reports_api():
    """Used by the report page filters: filtered sums without reload."""
    uid = current_user()["id"]
    rows, scope = _rows_for(uid)
    cur = current_user()["currency"]
    tin, net, tout = _totals(rows)
    data = _hydrate(rows, cur)
    from flask import jsonify
    percat = {}
    for r in rows:
        if r["nature"] == "expense":
            percat[r.get("category") or "Other"] = percat.get(
                r.get("category") or "Other", 0) + r["amount_paise"]
    return jsonify(scope=scope, n=len(data),
                   total_in=fmt_money(tin, cur), total_out=fmt_money(tout, cur),
                   net=fmt_money(net, cur),
                   percat={k: fmt_money(v, cur) for k, v in
                           sorted(percat.items(), key=lambda kv: -kv[1])})


@bp.get("/reports/download.csv")
@login_required
def download_csv():
    uid = current_user()["id"]
    rows, scope = _rows_for(uid)
    cur = current_user()["currency"]
    data = _hydrate(rows, cur)
    si = io.StringIO()
    w = csv.writer(si)
    w.writerow(COLS)
    for r in data:
        w.writerow([r["Date"], r["Time"], r["Direction"], r["Nature"],
                    r["Person"], r["Category"], r["Subcategory"],
                    r["Description"], r["Method"], r["UTR"],
                    r["Linked Loan"], fmt_money(r["Amount"], cur)])
    fn = "MyMoney_txn_report.csv"
    resp = Response(si.getvalue().encode("utf-8-sig"), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f"attachment; filename={fn}"
    return resp


@bp.get("/reports/download.xlsx")
@login_required
def download_xlsx():
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    uid = current_user()["id"]
    rows, scope = _rows_for(uid)
    cur = current_user()["currency"]
    data = _hydrate(rows, cur)
    tin, net, tout = _totals(rows)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transactions"
    ws.append([f"My Money — transaction report ({scope})"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([f"Generated {ist_fmt_date(now_ist_str()[:10])} —"
               f" {len(data)} rows of the current user only."])
    ws.append([])
    ws.append(COLS)
    for i in range(1, len(COLS) + 1):
        c = ws.cell(row=4, column=i)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", start_color="1F3A5F")
        c.alignment = Alignment(horizontal="center")
    for r in data:
        ws.append([r[c] if c != "Amount" else
                   round(r["Amount"] / 100, 2) for c in COLS])
    n = 4 + len(data)
    ws.append(["", "", "", "", "", "", "", "", "", "",
               "Money In", round(tin / 100, 2)])
    ws.append(["", "", "", "", "", "", "", "", "", "",
               "Money Out", round(tout / 100, 2)])
    ws.append(["", "", "", "", "", "", "", "", "", "",
               "Net flow", round(net / 100, 2)])
    for rr in range(n, n + 3):
        ws.cell(row=rr, column=12).number_format = "#,##,##0.00"
        ws.cell(row=rr, column=12).font = Font(bold=True)
        ws.cell(row=rr, column=11).font = Font(bold=True)
    widths = {"A": 11, "B": 6, "C": 9, "D": 16, "E": 18, "F": 14, "G": 14,
              "H": 30, "I": 12, "J": 18, "K": 11, "L": 12}
    for col, wd in widths.items():
        ws.column_dimensions[col].width = wd
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:L{4 + len(data)}"
    bio = io.BytesIO()
    wb.save(bio)
    resp = Response(bio.getvalue(),
                    mimetype="application/vnd.openxmlformats-officedocument"
                             ".spreadsheetml.sheet")
    resp.headers["Content-Disposition"] = "attachment; filename=MyMoney_txn_report.xlsx"
    return resp


def _pdf(data, scope, cur, uid_name) -> bytes:
    """Reportlab PDF with ₹ via an embedded Unicode font when available."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)

    rupee_font = None
    for fname in ("segoeui.ttf", "DejaVuSans.ttf", "FreeSans.ttf"):
        path = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts",
                            fname)
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("MMRupee", path))
                rupee_font = "MMRupee"
                break
            except Exception:
                continue
    cur_sym = "₹" if rupee_font else "Rs."      # Helvetica lacks U+20B9
    font = rupee_font or "Helvetica"
    font_b = rupee_font or "Helvetica-Bold"

    styles = getSampleStyleSheet()
    base = ParagraphStyle("MMBase", parent=styles["BodyText"], fontName=font,
                          fontSize=8, leading=10)
    head_sty = ParagraphStyle("MMH2", parent=base, fontName=font_b,
                              fontSize=8.5, textColor=colors.white,
                              leading=10)
    h = ParagraphStyle("MMTitle", parent=styles["Title"], fontSize=15)
    tol = ParagraphStyle("MMTot", parent=base, fontName=font_b, fontSize=9.5)

    def amt(paise: int) -> str:
        # fmt_money starts with the currency symbol; swap it for the PDF one
        digits = fmt_money(int(paise), cur)[1:].strip()
        return f"{cur_sym} {digits}"

    story = [Paragraph(f"My Money — transaction report ({scope})", h),
             Spacer(1, 4),
             Paragraph(f"Owner: {uid_name}. Contains only this account's data;"
                       f" amounts in {cur}.", base),
             Spacer(1, 8)]
    head = [Paragraph(c, head_sty) for c in COLS]
    body = [[Paragraph(str(r[c]) if c != "Amount" else amt(r["Amount"]), base)
             for c in COLS] for r in data]
    tdata = [head] + (body or [[Paragraph("", base)] * len(COLS)])
    table = Table(tdata, repeatRows=1, colWidths=[
        22 * mm, 10 * mm, 12 * mm, 24 * mm, 26 * mm, 20 * mm, 20 * mm,
        62 * mm, 18 * mm, 24 * mm, 20 * mm, 24 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B8C4D0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#F2F6FA")]),
        ("ALIGN", (-1, 1), (-1, -1), "RIGHT"),
    ]))
    story.append(table)
    tin = sum(r["Amount"] for r in data if r["Direction"] == "In")
    tout = sum(r["Amount"] for r in data if r["Direction"] == "Out")
    net = tin - tout
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f"Row count: {len(data)} — Totals — Money In: {amt(tin)} · "
        f"Money Out: {amt(tout)} · Net flow: {amt(net)}", tol))
    bio = io.BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=landscape(A4),
                            leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=10 * mm, bottomMargin=10 * mm,
                            title="My Money report")
    doc.build(story)
    return bio.getvalue()


@bp.get("/reports/download.pdf")
@login_required
def download_pdf():
    uid = current_user()["id"]
    rows, scope = _rows_for(uid)
    cur = current_user()["currency"]
    data = _hydrate(rows, cur)
    out = _pdf(data, scope, cur, current_user()["username"])
    resp = Response(out, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = "attachment; filename=MyMoney_txn_report.pdf"
    return resp
