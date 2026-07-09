"""
Run this from the loan-backend-clean repo root:

    python apply_report_fix.py

It replaces the get_defaulters_report and get_uncollected_dues_report
functions in app/routes/dashboard_routes.py with versions that match
the payments-report letterhead style. Makes a .bak backup first.
"""
import re
import shutil
import sys
from pathlib import Path

TARGET = Path("app/routes/dashboard_routes.py")

NEW_DEFAULTERS = '''@router.get("/defaulters-report")
def get_defaulters_report(
    end_date: str | None = None,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    PDF report of defaulter loans.
    Definition: ACTIVE loans with is_defaulter == true
    Mirrors get_defaulters_list's query/fields, rendered as a PDF.
    """
    from io import BytesIO
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    from fastapi.responses import StreamingResponse
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.enums import TA_RIGHT, TA_CENTER
    from sqlalchemy.orm import selectinload

    report_date = end_date or now_eat().date().isoformat()

    defaulters = db.query(Loan).options(selectinload(Loan.customer)).filter(
        Loan.is_defaulter == True,
        Loan.status == LoanStatus.ACTIVE,
    ).order_by(Loan.defaulter_flagged_date.desc()).all()

    total_daily_instalments = sum(float(d.daily_instalment or 0) for d in defaulters)

    NAVY     = colors.HexColor("#0f2942")
    SLATE    = colors.HexColor("#475569")
    LIGHT_BG = colors.HexColor("#f8fafc")
    BORDER   = colors.HexColor("#cbd5e1")
    GOLD     = colors.HexColor("#c9a84c")

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=14*mm, bottomMargin=14*mm,
        leftMargin=18*mm, rightMargin=18*mm,
    )
    base = getSampleStyleSheet()

    inst_style   = ParagraphStyle("DF_Inst",  parent=base["Normal"], fontName="Helvetica-Bold",    fontSize=17, textColor=NAVY,  leading=20)
    tag_style    = ParagraphStyle("DF_Tag",   parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8,  textColor=GOLD,  leading=10)
    rt_style     = ParagraphStyle("DF_RT",    parent=base["Normal"], fontName="Helvetica-Bold",    fontSize=9,  textColor=NAVY,  leading=11, alignment=TA_RIGHT)
    rs_style     = ParagraphStyle("DF_RS",    parent=base["Normal"], fontName="Helvetica",         fontSize=8,  textColor=SLATE, leading=10, alignment=TA_RIGHT)
    sl_style     = ParagraphStyle("DF_SL",    parent=base["Normal"], fontName="Helvetica",         fontSize=7.5,textColor=SLATE, leading=10, alignment=TA_CENTER)
    sv_style     = ParagraphStyle("DF_SV",    parent=base["Normal"], fontName="Helvetica-Bold",    fontSize=13, textColor=NAVY,  leading=16, alignment=TA_CENTER)
    footer_style = ParagraphStyle("DF_Ftr",   parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=7,  textColor=SLATE, leading=10, alignment=TA_CENTER)

    story = []

    left_tbl = Table(
        [[Paragraph("KODONGO SAVINGS & CREDIT", inst_style)],
         [Paragraph("Trusted Financial Solutions", tag_style)]],
        colWidths=[None],
    )
    left_tbl.setStyle(TableStyle([
        ("LEFTPADDING",   (0,0),(-1,-1), 0), ("RIGHTPADDING",  (0,0),(-1,-1), 0),
        ("TOPPADDING",    (0,0),(-1,-1), 0), ("BOTTOMPADDING", (0,0),(-1,-1), 2),
    ]))
    right_tbl = Table(
        [[Paragraph("DEFAULTERS REPORT", rt_style)],
         [Paragraph(f"As of: {report_date}", rs_style)],
         [Paragraph(f"Generated: {_dt.now(ZoneInfo('Africa/Nairobi')).strftime('%d %b %Y, %H:%M')} EAT", rs_style)]],
        colWidths=[None],
    )
    right_tbl.setStyle(TableStyle([
        ("LEFTPADDING",   (0,0),(-1,-1), 0), ("RIGHTPADDING",  (0,0),(-1,-1), 0),
        ("TOPPADDING",    (0,0),(-1,-1), 0), ("BOTTOMPADDING", (0,0),(-1,-1), 2),
    ]))
    hdr = Table([[left_tbl, right_tbl]], colWidths=["60%","40%"])
    hdr.setStyle(TableStyle([
        ("VALIGN", (0,0),(-1,-1), "TOP"),
        ("LEFTPADDING",  (0,0),(-1,-1), 0), ("RIGHTPADDING", (0,0),(-1,-1), 0),
    ]))
    story.append(hdr)
    story.append(Spacer(1, 5))
    story.append(HRFlowable(width="100%", thickness=2.5, color=NAVY, spaceAfter=2))
    story.append(HRFlowable(width="100%", thickness=1,   color=GOLD, spaceAfter=10))

    sum_tbl = Table(
        [[Paragraph("TOTAL DEFAULTERS", sl_style), Paragraph("TOTAL DAILY INSTALMENTS", sl_style)],
         [Paragraph(str(len(defaulters)), sv_style), Paragraph(f"KES {total_daily_instalments:,.2f}", sv_style)]],
        colWidths=["30%", "70%"],
    )
    sum_tbl.setStyle(TableStyle([
        ("BOX",           (0,0),(-1,-1), 0.75, BORDER),
        ("LINEAFTER",     (0,0),(0,-1),  0.5,  BORDER),
        ("TOPPADDING",    (0,0),(-1,-1), 6),
        ("BOTTOMPADDING", (0,0),(-1,-1), 6),
    ]))
    story.append(sum_tbl)
    story.append(Spacer(1, 14))

    if not defaulters:
        story.append(Paragraph("No defaulters found.", base["Normal"]))
    else:
        rows = [["#", "CUSTOMER", "PHONE", "ID NUMBER", "LOAN BALANCE (KES)", "DAILY INSTALMENT (KES)", "FLAGGED DATE"]]
        for idx, d in enumerate(defaulters, 1):
            customer = d.customer
            rows.append([
                str(idx),
                customer.name if customer else "-",
                customer.phone if customer else "-",
                customer.id_number if customer else "-",
                f"{float(d.remaining_amount):,.2f}" if d.remaining_amount is not None else "-",
                f"{float(d.daily_instalment):,.2f}" if d.daily_instalment is not None else "-",
                d.defaulter_flagged_date.strftime("%d %b %Y") if d.defaulter_flagged_date else "-",
            ])

        tbl = Table(rows, repeatRows=1,
                    colWidths=[8*mm, 36*mm, 26*mm, 26*mm, 28*mm, 30*mm, 24*mm])
        tbl.setStyle(TableStyle([
            ("FONTNAME",       (0,0),(-1, 0), "Helvetica-Bold"),
            ("FONTNAME",       (0,1),(-1,-1), "Helvetica"),
            ("FONTSIZE",       (0,0),(-1,-1), 7.5),
            ("TEXTCOLOR",      (0,0),(-1, 0), SLATE),
            ("BACKGROUND",     (0,0),(-1, 0), LIGHT_BG),
            ("ALIGN",          (0,0),(0,-1),  "CENTER"),
            ("ALIGN",          (1,0),(3,-1),  "LEFT"),
            ("ALIGN",          (4,0),(5,-1),  "RIGHT"),
            ("ALIGN",          (6,0),(6,-1),  "CENTER"),
            ("LINEBELOW",      (0,0),(-1, 0), 0.75, BORDER),
            ("LINEBELOW",      (0,1),(-1,-2), 0.35, BORDER),
            ("BOX",            (0,0),(-1,-1), 0.75, BORDER),
            ("ROWBACKGROUNDS", (0,1),(-1,-1), [colors.white, LIGHT_BG]),
            ("TOPPADDING",     (0,0),(-1,-1), 4),
            ("BOTTOMPADDING",  (0,0),(-1,-1), 4),
            ("LEFTPADDING",    (0,0),(-1,-1), 5),
            ("RIGHTPADDING",   (0,0),(-1,-1), 5),
        ]))
        story.append(tbl)

    story.append(Spacer(1, 18))
    story.append(HRFlowable(width="100%", thickness=0.75, color=BORDER, spaceAfter=6))
    story.append(Paragraph(
        f"Generated on {_dt.now(ZoneInfo('Africa/Nairobi')).strftime('%d %B %Y at %H:%M EAT')}. "
        f"This report is for internal use only. Kodongo Savings & Credit.",
        footer_style,
    ))

    doc.build(story)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=defaulters_report_{report_date}.pdf"
        },
    )
'''

NEW_UNCOLLECTED = '''@router.get("/uncollected-dues-report")
def get_uncollected_dues_report(
    start_date: str,
    end_date: str,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    PDF report of uncollected dues for the given date.
    """
    from io import BytesIO
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    from fastapi.responses import StreamingResponse
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.enums import TA_RIGHT, TA_CENTER

    items = _calc_uncollected_dues(db, start_date, end_date)

    total_uncollected = sum(float(row["loan_balance"] or 0) for row in items)

    NAVY     = colors.HexColor("#0f2942")
    SLATE    = colors.HexColor("#475569")
    LIGHT_BG = colors.HexColor("#f8fafc")
    BORDER   = colors.HexColor("#cbd5e1")
    GOLD     = colors.HexColor("#c9a84c")

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=14*mm, bottomMargin=14*mm,
        leftMargin=18*mm, rightMargin=18*mm,
    )
    base = getSampleStyleSheet()

    inst_style   = ParagraphStyle("UD_Inst",  parent=base["Normal"], fontName="Helvetica-Bold",    fontSize=17, textColor=NAVY,  leading=20)
    tag_style    = ParagraphStyle("UD_Tag",   parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8,  textColor=GOLD,  leading=10)
    rt_style     = ParagraphStyle("UD_RT",    parent=base["Normal"], fontName="Helvetica-Bold",    fontSize=9,  textColor=NAVY,  leading=11, alignment=TA_RIGHT)
    rs_style     = ParagraphStyle("UD_RS",    parent=base["Normal"], fontName="Helvetica",         fontSize=8,  textColor=SLATE, leading=10, alignment=TA_RIGHT)
    sl_style     = ParagraphStyle("UD_SL",    parent=base["Normal"], fontName="Helvetica",         fontSize=7.5,textColor=SLATE, leading=10, alignment=TA_CENTER)
    sv_style     = ParagraphStyle("UD_SV",    parent=base["Normal"], fontName="Helvetica-Bold",    fontSize=13, textColor=NAVY,  leading=16, alignment=TA_CENTER)
    footer_style = ParagraphStyle("UD_Ftr",   parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=7,  textColor=SLATE, leading=10, alignment=TA_CENTER)

    story = []

    left_tbl = Table(
        [[Paragraph("KODONGO SAVINGS & CREDIT", inst_style)],
         [Paragraph("Trusted Financial Solutions", tag_style)]],
        colWidths=[None],
    )
    left_tbl.setStyle(TableStyle([
        ("LEFTPADDING",   (0,0),(-1,-1), 0), ("RIGHTPADDING",  (0,0),(-1,-1), 0),
        ("TOPPADDING",    (0,0),(-1,-1), 0), ("BOTTOMPADDING", (0,0),(-1,-1), 2),
    ]))
    right_tbl = Table(
        [[Paragraph("UNCOLLECTED DUES REPORT", rt_style)],
         [Paragraph(f"Period: {start_date} - {end_date}", rs_style)],
         [Paragraph(f"Generated: {_dt.now(ZoneInfo('Africa/Nairobi')).strftime('%d %b %Y, %H:%M')} EAT", rs_style)]],
        colWidths=[None],
    )
    right_tbl.setStyle(TableStyle([
        ("LEFTPADDING",   (0,0),(-1,-1), 0), ("RIGHTPADDING",  (0,0),(-1,-1), 0),
        ("TOPPADDING",    (0,0),(-1,-1), 0), ("BOTTOMPADDING", (0,0),(-1,-1), 2),
    ]))
    hdr = Table([[left_tbl, right_tbl]], colWidths=["60%","40%"])
    hdr.setStyle(TableStyle([
        ("VALIGN", (0,0),(-1,-1), "TOP"),
        ("LEFTPADDING",  (0,0),(-1,-1), 0), ("RIGHTPADDING", (0,0),(-1,-1), 0),
    ]))
    story.append(hdr)
    story.append(Spacer(1, 5))
    story.append(HRFlowable(width="100%", thickness=2.5, color=NAVY, spaceAfter=2))
    story.append(HRFlowable(width="100%", thickness=1,   color=GOLD, spaceAfter=10))

    sum_tbl = Table(
        [[Paragraph("TOTAL CUSTOMERS", sl_style), Paragraph("TOTAL UNCOLLECTED (KES)", sl_style)],
         [Paragraph(str(len(items)), sv_style), Paragraph(f"KES {total_uncollected:,.2f}", sv_style)]],
        colWidths=["30%", "70%"],
    )
    sum_tbl.setStyle(TableStyle([
        ("BOX",           (0,0),(-1,-1), 0.75, BORDER),
        ("LINEAFTER",     (0,0),(0,-1),  0.5,  BORDER),
        ("TOPPADDING",    (0,0),(-1,-1), 6),
        ("BOTTOMPADDING", (0,0),(-1,-1), 6),
    ]))
    story.append(sum_tbl)
    story.append(Spacer(1, 14))

    if not items:
        story.append(Paragraph("All dues have been collected for this date.", base["Normal"]))
    else:
        rows = [["#", "CUSTOMER", "PHONE", "DAILY INSTALMENT (KES)", "LOAN BALANCE (KES)", "SKIPPED DAYS"]]
        for idx, row in enumerate(items, 1):
            rows.append([
                str(idx),
                row["customer_name"] or "-",
                row["customer_phone"] or "-",
                f"{float(row['daily_instalment']):,.2f}",
                f"{float(row['loan_balance']):,.2f}",
                str(row["skipped_days"]),
            ])

        tbl = Table(rows, repeatRows=1,
                    colWidths=[8*mm, 42*mm, 30*mm, 32*mm, 32*mm, 26*mm])
        tbl.setStyle(TableStyle([
            ("FONTNAME",       (0,0),(-1, 0), "Helvetica-Bold"),
            ("FONTNAME",       (0,1),(-1,-1), "Helvetica"),
            ("FONTSIZE",       (0,0),(-1,-1), 7.5),
            ("TEXTCOLOR",      (0,0),(-1, 0), SLATE),
            ("BACKGROUND",     (0,0),(-1, 0), LIGHT_BG),
            ("ALIGN",          (0,0),(0,-1),  "CENTER"),
            ("ALIGN",          (1,0),(2,-1),  "LEFT"),
            ("ALIGN",          (3,0),(4,-1),  "RIGHT"),
            ("ALIGN",          (5,0),(5,-1),  "CENTER"),
            ("LINEBELOW",      (0,0),(-1, 0), 0.75, BORDER),
            ("LINEBELOW",      (0,1),(-1,-2), 0.35, BORDER),
            ("BOX",            (0,0),(-1,-1), 0.75, BORDER),
            ("ROWBACKGROUNDS", (0,1),(-1,-1), [colors.white, LIGHT_BG]),
            ("TOPPADDING",     (0,0),(-1,-1), 4),
            ("BOTTOMPADDING",  (0,0),(-1,-1), 4),
            ("LEFTPADDING",    (0,0),(-1,-1), 5),
            ("RIGHTPADDING",   (0,0),(-1,-1), 5),
        ]))
        story.append(tbl)

    story.append(Spacer(1, 18))
    story.append(HRFlowable(width="100%", thickness=0.75, color=BORDER, spaceAfter=6))
    story.append(Paragraph(
        f"Generated on {_dt.now(ZoneInfo('Africa/Nairobi')).strftime('%d %B %Y at %H:%M EAT')}. "
        f"This report is for internal use only. Kodongo Savings & Credit.",
        footer_style,
    ))

    doc.build(story)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=uncollected_dues_report_{end_date}.pdf"
        },
    )
'''


def replace_function(text, func_signature_regex, new_func_text, label):
    """
    Finds a top-level function starting with func_signature_regex (matched
    against the @router.get(...) decorator line) and replaces it up to
    (but not including) the next top-level @router.get or 'def ' at column 0,
    or end of file.
    """
    pattern = re.compile(
        func_signature_regex + r'.*?(?=\n@router\.get|\n# =+|\Z)',
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        print(f"ERROR: Could not find {label} in file. No changes made for this function.")
        return text, False
    start, end = match.span()
    new_text = text[:start] + new_func_text.rstrip() + "\n" + text[end:]
    print(f"OK: Replaced {label} ({end - start} chars -> {len(new_func_text)} chars)")
    return new_text, True


def main():
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found. Run this script from the repo root.")
        sys.exit(1)

    backup_path = TARGET.with_suffix(".py.bak")
    shutil.copy(TARGET, backup_path)
    print(f"Backup written to {backup_path}")

    text = TARGET.read_text(encoding="utf-8")

    text, ok1 = replace_function(
        text,
        re.escape('@router.get("/defaulters-report")\ndef get_defaulters_report('),
        NEW_DEFAULTERS,
        "get_defaulters_report",
    )
    text, ok2 = replace_function(
        text,
        re.escape('@router.get("/uncollected-dues-report")\ndef get_uncollected_dues_report('),
        NEW_UNCOLLECTED,
        "get_uncollected_dues_report",
    )

    if not (ok1 and ok2):
        print("One or more replacements failed. File NOT written. Check the patterns above.")
        sys.exit(1)

    TARGET.write_text(text, encoding="utf-8")
    print(f"Wrote updated {TARGET}")
    print("Now run: python -m py_compile app/routes/dashboard_routes.py")


if __name__ == "__main__":
    main()
