"""
CORRECTED Dashboard Routes
- Metrics based on CORRECT definitions of ACTIVE, OVERDUE, DEFAULTERS
- ACTIVE = Days 1-30 from creation
- OVERDUE = Day 31+ from creation (tracked via Arrears)
- DEFAULTERS = ACTIVE loans with 5-day payment < required amount
"""

from fastapi import APIRouter, Depends, HTTPException
import time
from app.utils.timezone import now_eat
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta

from app.database import get_sync_db
from app.models import Loan, Arrears, Installment, LoanStatus, Customer
from app.services.loan_service import LoanService
from app.auth import get_current_user
from app.utils.pdf_report import render_pdf_report

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ============ ENDPOINTS ============

@router.get("/metrics")
def get_dashboard_metrics(
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Get dashboard key metrics.
    
    ACTIVE LOANS (Days 1-30):
    - active_loans: count of loans with status == ACTIVE
    - active_loans_outstanding: sum of remaining_amount for ACTIVE loans
    
    OVERDUE LOANS (Day 31+):
    - overdue_loans: count of Arrears with is_cleared == false
    - overdue_outstanding: sum of remaining_amount from Arrears
    
    DEFAULTERS (Subset of ACTIVE):
    - defaulters: count of is_defaulter == true AND status == ACTIVE
    
    COMPLETED:
    - completed_loans: count of status == COMPLETED
    - completed_amount: total_amount of completed loans
    """
    # Sync all loans first

    start_time = time.time()
    # Get metrics
    metrics = LoanService.get_loan_dashboard_metrics(db)
    elapsed = time.time() - start_time
    print(f">>> DASHBOARD /metrics took {elapsed:.3f}s", flush=True)
    return metrics


@router.get("/summary")
def get_dashboard_summary(
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Get dashboard summary with time-based metrics.
    
    Includes:
    - Last 3 months completed loans
    - This month active loans
    - Interest earned
    - Daily/weekly/monthly collection totals
    """
    # Sync all loans
    start_time = time.time()

    now = now_eat()
    three_months_ago = now - timedelta(days=90)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Completed loans in last 3 months
    completed_last_3_months = db.query(func.count(Loan.id)).filter(
        Loan.status == LoanStatus.COMPLETED,
        Loan.completed_at >= three_months_ago,
    ).scalar()

    completed_amount_3_months = db.query(func.sum(Loan.total_amount)).filter(
        Loan.status == LoanStatus.COMPLETED,
        Loan.completed_at >= three_months_ago,
    ).scalar() or 0

    # Active loans created this month
    active_this_month = db.query(func.count(Loan.id)).filter(
        Loan.status == LoanStatus.ACTIVE,
        Loan.start_date >= month_start,
    ).scalar()

    # Interest earned (sum of completed loans' interest)
    interest_earned = db.query(
        func.sum(Loan.total_amount - Loan.amount)
    ).filter(
        Loan.status == LoanStatus.COMPLETED,
        Loan.completed_at >= three_months_ago,
    ).scalar() or 0

    # Collections
    payments_today = db.query(func.sum(Installment.amount)).filter(
        Installment.payment_date >= today_start,
    ).scalar() or 0

    payments_this_week = db.query(func.sum(Installment.amount)).filter(
        Installment.payment_date >= week_start,
    ).scalar() or 0

    payments_this_month = db.query(func.sum(Installment.amount)).filter(
        Installment.payment_date >= month_start,
    ).scalar() or 0

    # Completed loans amount specifically THIS month (not last 3 months)
    completed_amount_this_month = db.query(func.sum(Loan.total_amount)).filter(
        Loan.status == LoanStatus.COMPLETED,
        Loan.completed_at >= month_start,
    ).scalar() or 0

    # Overdue count (last 3 months window, to match frontend's expected field)
    overdue_count_last_three_months = db.query(func.count(Arrears.id)).filter(
        Arrears.is_cleared == False,
        Arrears.arrears_date >= three_months_ago,
    ).scalar() or 0

    # Total customers in the system
    total_customers = db.query(func.count(Customer.id)).scalar() or 0

    # Defaulters count: ACTIVE loans flagged is_defaulter == true
    # (same definition used by the Defaulters page/report)
    defaulters_count = db.query(func.count(Loan.id)).filter(
        Loan.is_defaulter == True,
        Loan.status == LoanStatus.ACTIVE,
    ).scalar() or 0

    elapsed = time.time() - start_time
    print(f">>> DASHBOARD /summary took {elapsed:.3f}s", flush=True)


    return {
        # Frontend-expected field names
        "total_paid_today": payments_today,
        "total_paid_this_week": payments_this_week,
        "total_paid_this_month": payments_this_month,
        "completed_loans_amount_this_month": completed_amount_this_month,
        "interest_last_three_months": interest_earned,
        "total_customers": total_customers,
        "defaulters_count": defaulters_count,
        "overdue_count_last_three_months": overdue_count_last_three_months,
        "arrears_count_last_three_months": overdue_count_last_three_months,
        "completed_count_last_3_months": completed_last_3_months,
        "completed_amount_last_3_months": completed_amount_3_months,
        "active_loans_count_this_month": active_this_month,
        "interest_earned_last_3_months": interest_earned,
        "payments_collected_today": payments_today,
        "payments_collected_this_week": payments_this_week,
        "payments_collected_this_month": payments_this_month,
    }


@router.get("/trends")
def get_trends(
    months: int = 3,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Get monthly Returns & Interest trends for completed loans (last N months).
    """
    def _month_start(dt):
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    now = now_eat()
    current_month = _month_start(now)

    months_list = []
    m = current_month
    for _ in range(months):
        months_list.append(m)
        if m.month == 1:
            m = m.replace(year=m.year - 1, month=12)
        else:
            m = m.replace(month=m.month - 1)
    months_list = sorted(months_list)

    range_start = months_list[0]

    completed_loans = db.query(Loan).filter(
        Loan.status == LoanStatus.COMPLETED,
        Loan.completed_at >= range_start,
    ).all()

    from collections import defaultdict as _defaultdict
    monthly = _defaultdict(lambda: {"returns": 0.0, "interest": 0.0})
    for loan in completed_loans:
        if not loan.completed_at:
            continue
        key = loan.completed_at.strftime("%b %Y")
        monthly[key]["returns"] += float(loan.total_amount or 0)
        monthly[key]["interest"] += float((loan.total_amount or 0) - (loan.amount or 0))

    result = []
    for m in months_list:
        key = m.strftime("%b %Y")
        data = monthly.get(key, {"returns": 0.0, "interest": 0.0})
        result.append({
            "month": key,
            "returns": round(data["returns"], 2),
            "interest": round(data["interest"], 2),
        })

    return result


@router.get("/defaulters")
def get_defaulters_list(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Get list of defaulter loans.

    TRUE Definition: ACTIVE loans with 5 OR MORE CONSECUTIVE DAYS (ending
    today) where that specific day's payment fell short of the daily
    instalment. This replaced the old lifetime-cumulative is_defaulter
    check, which flagged loans after just 1 day behind (that concept is
    now served by the separate /arrears endpoint instead).
    """
    from sqlalchemy.orm import selectinload
    from app.models import Customer
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    from app.models import Installment as _Installment
    from collections import defaultdict as _defaultdict

    today = now_eat().date()

    # Load all ACTIVE loans (candidates) - filtering to >=5 days happens in Python below
    candidates = db.query(Loan).options(selectinload(Loan.customer)).filter(
        Loan.status == LoanStatus.ACTIVE,
    ).all()

    loan_ids = [d.id for d in candidates]
    all_installments = db.query(_Installment).filter(
        _Installment.loan_id.in_(loan_ids),
        func.date(_Installment.payment_date) <= today,
    ).all() if loan_ids else []

    sums_by_loan = _defaultdict(lambda: _defaultdict(float))
    for inst in all_installments:
        d = inst.payment_date.date() if isinstance(inst.payment_date, _dt) else inst.payment_date
        sums_by_loan[inst.loan_id][d] += float(inst.amount or 0)

    def _days_defaulted(loan):
        daily = loan.daily_instalment
        sums = sums_by_loan[loan.id]
        start = loan.start_date.date() if isinstance(loan.start_date, _dt) else loan.start_date
        if not start:
            return 0
        skipped = 0
        current = today
        while current >= start:
            paid = sums.get(current, 0.0)
            if paid < daily - 0.01:
                skipped += 1
                current -= _td(days=1)
            else:
                break
        return skipped

    # TRUE defaulter rule: 5+ consecutive skipped days
    defaulters_with_days = [(d, _days_defaulted(d)) for d in candidates]
    defaulters_with_days = [(d, days) for d, days in defaulters_with_days if days >= 5]
    defaulters_with_days.sort(key=lambda pair: pair[1], reverse=True)

    total = len(defaulters_with_days)
    page = defaulters_with_days[offset:offset + limit]

    return {
        "items": [
            {
                "id": d.id,
                "loan_id": d.id,
                "customer_id": d.customer_id,
                "id_number": d.customer.id_number if d.customer else d.customer_id,
                "customer_name": d.customer.name if d.customer else None,
                "phone": d.customer.phone if d.customer else None,
                "amount": d.amount,
                "loan_amount": d.amount,
                "total_amount": d.total_amount,
                "remaining_amount": d.remaining_amount,
                "loan_balance": d.remaining_amount,
                "daily_instalment": d.daily_instalment,
                "days_since_start": d.days_since_start,
                "days_defaulted": days,
                "start_date": d.start_date,
                "date_loan_taken": d.start_date,
                "due_date": d.due_date,
                "status": d.status.value,
                "customer": ({
                    "name": d.customer.name,
                    "id_number": d.customer.id_number,
                    "phone": d.customer.phone,
                    "location": d.customer.location,
                } if d.customer else None),
            }
            for d, days in page
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/defaulters-report")
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

# ============ REPORT ENDPOINTS ============

@router.get("/reports/active-loans-summary")
def active_loans_report(
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Summary report of active loans.
    
    Shows:
    - Total active loans
    - Total outstanding (all active loans)
    - Breakdown by customer
    """
    active_loans = db.query(Loan).filter(
        Loan.status == LoanStatus.ACTIVE,
    ).all()

    total_active = len(active_loans)
    total_outstanding = sum(l.remaining_amount for l in active_loans)
    avg_remaining = total_outstanding / total_active if total_active > 0 else 0

    return {
        "total_active_loans": total_active,
        "total_outstanding": total_outstanding,
        "average_remaining_per_loan": avg_remaining,
        "loans": [
            {
                "id": l.id,
                "customer_id": l.customer_id,
                "amount": l.amount,
                "remaining_amount": l.remaining_amount,
                "days_since_start": l.days_since_start,
            }
            for l in active_loans
        ],
    }


@router.get("/reports/overdue-summary")
def overdue_report(
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Summary report of overdue loans.
    
    Shows:
    - Total overdue loans
    - Total outstanding on overdue loans
    - Breakdown by customer
    """
    overdue_arrears = db.query(Arrears).filter(
        Arrears.is_cleared == False,
    ).all()

    total_overdue = len(overdue_arrears)
    total_outstanding = sum(a.remaining_amount for a in overdue_arrears)
    avg_remaining = total_outstanding / total_overdue if total_overdue > 0 else 0

    return {
        "total_overdue_loans": total_overdue,
        "total_outstanding": total_outstanding,
        "average_remaining_per_loan": avg_remaining,
        "arrears": [
            {
                "id": a.id,
                "loan_id": a.loan_id,
                "customer_id": a.customer_id,
                "original_amount": a.original_amount,
                "remaining_amount": a.remaining_amount,
                "arrears_date": a.arrears_date,
                "days_overdue": (now_eat() - a.arrears_date).days,
            }
            for a in overdue_arrears
        ],
    }


@router.get("/overdue-report")
def get_overdue_report(
    start_date: str | None = None,
    end_date: str | None = None,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    PDF report of overdue loans (active arrears).
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

    range_label = f"{start_date} to {end_date}" if (start_date and end_date) else (f"From {start_date}" if start_date else (f"Up to {end_date}" if end_date else report_date))
    report_date = now_eat().date().isoformat()
    query = db.query(Arrears).options(selectinload(Arrears.customer)).filter(
        Arrears.is_cleared == False,
    )
    if start_date:
        query = query.filter(Arrears.arrears_date >= _dt.strptime(start_date, "%Y-%m-%d").date())
    if end_date:
        query = query.filter(Arrears.arrears_date <= _dt.strptime(end_date, "%Y-%m-%d").date())
    overdue_arrears = query.order_by(Arrears.arrears_date.desc()).all()

    total_outstanding = sum(float(a.remaining_amount or 0) for a in overdue_arrears)

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

    inst_style   = ParagraphStyle("OD_Inst",  parent=base["Normal"], fontName="Helvetica-Bold",    fontSize=17, textColor=NAVY,  leading=20)
    tag_style    = ParagraphStyle("OD_Tag",   parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8,  textColor=GOLD,  leading=10)
    rt_style     = ParagraphStyle("OD_RT",    parent=base["Normal"], fontName="Helvetica-Bold",    fontSize=9,  textColor=NAVY,  leading=11, alignment=TA_RIGHT)
    rs_style     = ParagraphStyle("OD_RS",    parent=base["Normal"], fontName="Helvetica",         fontSize=8,  textColor=SLATE, leading=10, alignment=TA_RIGHT)
    sl_style     = ParagraphStyle("OD_SL",    parent=base["Normal"], fontName="Helvetica",         fontSize=7.5,textColor=SLATE, leading=10, alignment=TA_CENTER)
    sv_style     = ParagraphStyle("OD_SV",    parent=base["Normal"], fontName="Helvetica-Bold",    fontSize=13, textColor=NAVY,  leading=16, alignment=TA_CENTER)
    footer_style = ParagraphStyle("OD_Ftr",   parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=7,  textColor=SLATE, leading=10, alignment=TA_CENTER)

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
        [[Paragraph("OVERDUE LOANS REPORT", rt_style)],
         [Paragraph(f"As of: {range_label}", rs_style)],
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
        [[Paragraph("TOTAL OVERDUE LOANS", sl_style), Paragraph("TOTAL OUTSTANDING", sl_style)],
         [Paragraph(str(len(overdue_arrears)), sv_style), Paragraph(f"KES {total_outstanding:,.2f}", sv_style)]],
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

    if not overdue_arrears:
        story.append(Paragraph("No overdue loans found.", base["Normal"]))
    else:
        rows = [["#", "CUSTOMER", "PHONE", "LOAN ID", "ORIGINAL (KES)", "REMAINING (KES)", "ARREARS DATE"]]
        for idx, a in enumerate(overdue_arrears, 1):
            customer = a.customer
            rows.append([
                str(idx),
                customer.name if customer else "-",
                customer.phone if customer else "-",
                str(a.loan_id),
                f"{float(a.original_amount):,.2f}" if a.original_amount is not None else "-",
                f"{float(a.remaining_amount):,.2f}" if a.remaining_amount is not None else "-",
                a.arrears_date.strftime("%d %b %Y") if a.arrears_date else "-",
            ])

        tbl = Table(rows, repeatRows=1,
                    colWidths=[8*mm, 36*mm, 26*mm, 18*mm, 28*mm, 28*mm, 26*mm])
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
            "Content-Disposition": f"attachment; filename=overdue_report_{report_date}.pdf"
        },
    )

@router.get("/reports/defaulters-summary")
def defaulters_report(
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Summary report of defaulter loans.
    
    Definition: ACTIVE loans with is_defaulter == true
    
    Shows:
    - Total defaulters
    - Total outstanding on defaulted loans
    """
    defaulters = db.query(Loan).filter(
        Loan.is_defaulter == True,
        Loan.status == LoanStatus.ACTIVE,
    ).all()

    total_defaulters = len(defaulters)
    total_outstanding = sum(d.remaining_amount for d in defaulters)
    avg_remaining = total_outstanding / total_defaulters if total_defaulters > 0 else 0

    return {
        "total_defaulters": total_defaulters,
        "total_outstanding": total_outstanding,
        "average_remaining_per_loan": avg_remaining,
        "defaulters": [
            {
                "id": d.id,
                "customer_id": d.customer_id,
                "amount": d.amount,
                "remaining_amount": d.remaining_amount,
                "days_since_start": d.days_since_start,
                "flagged_date": d.defaulter_flagged_date,
            }
            for d in defaulters
        ],
    }





# ============ UNCOLLECTED DUES ============

def _calc_uncollected_dues(db: Session, start_date_str: str, end_date_str: str):
    """
    Shared logic for the JSON and PDF report endpoints.

    Definition: ACTIVE/OVERDUE loans that are behind on payments within the
    selected From-To window, i.e. cumulative amount paid within [from_date,
    to_date] is less than cumulative amount expected (daily_instalment x
    days elapsed) within that same window. This is "forgiving": a customer
    who underpaid on one day but caught up later within the window will NOT
    appear if their window-cumulative arrears is back to zero or positive.

    skipped_days = number of consecutive days (ending at to_date, never
    going before window_start) where that specific day's payment fell
    short of the daily instalment.
    """
    from_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    to_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    if from_date > to_date:
        from_date, to_date = to_date, from_date

    # Load all active/overdue loans in one query
    from sqlalchemy.orm import selectinload
    loans = db.query(Loan).options(selectinload(Loan.customer)).filter(
        Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.OVERDUE]),
    ).all()

    # Filter to loans whose active window includes to_date, and compute
    # each loan's effective window_start (loan start, clamped to from_date)
    eligible = []
    for loan in loans:
        if not loan.start_date:
            continue
        loan_start = loan.start_date.date() if isinstance(loan.start_date, datetime) else loan.start_date
        if to_date < loan_start:
            continue
        last_day = loan_start + timedelta(days=29)
        if to_date > last_day:
            continue
        window_start = max(loan_start, from_date)
        if window_start > to_date:
            continue
        eligible.append((loan, window_start))

    if not eligible:
        return []

    # Fetch ALL installments for eligible loans in a single query
    loan_ids = [loan.id for loan, _ in eligible]
    all_installments = db.query(Installment).filter(
        Installment.loan_id.in_(loan_ids),
        func.date(Installment.payment_date) <= to_date,
    ).all()

    # Group installments by (loan_id, date)
    from collections import defaultdict
    sums: dict = defaultdict(lambda: defaultdict(float))
    for inst in all_installments:
        d = inst.payment_date.date() if isinstance(inst.payment_date, datetime) else inst.payment_date
        sums[inst.loan_id][d] += float(inst.amount or 0)

    items = []
    for loan, window_start in eligible:
        daily_instalment = loan.daily_instalment
        sums_by_date = sums[loan.id]

        # Window-bounded cumulative arrears: paid vs expected within
        # [window_start, to_date] only. Forgiving: a catch-up payment
        # later in the window offsets an earlier miss.
        elapsed_days = (to_date - window_start).days + 1
        window_expected = daily_instalment * elapsed_days
        window_paid = sum(
            amt for d, amt in sums_by_date.items() if window_start <= d <= to_date
        )
        arrears = window_paid - window_expected

        if arrears >= -0.01:
            continue  # not behind within this window

        # Skipped days: consecutive days ending at to_date, never going
        # before window_start, where that day's payment fell short.
        skipped_days = 0
        current = to_date
        while current >= window_start:
            paid = sums_by_date.get(current, 0.0)
            if paid < daily_instalment - 0.01:
                skipped_days += 1
                current -= timedelta(days=1)
            else:
                break

        customer = loan.customer
        items.append({
            "loan_id": loan.id,
            "customer_name": customer.name if customer else None,
            "customer_phone": customer.phone if customer else None,
            "customer_id_number": loan.customer_id,
            "daily_instalment": daily_instalment,
            "loan_balance": float(loan.remaining_amount if loan.remaining_amount is not None else loan.total_amount),
            "skipped_days": skipped_days,
            "arrears": arrears,
        })

    items.sort(key=lambda r: r["skipped_days"], reverse=True)
    return items


@router.get("/uncollected-dues")
def get_uncollected_dues(
    date: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Get loans (any non-completed status, balance > 0) whose instalment for
    the given single `date` (default: today) has not been collected.

    This replaced the old date-range cumulative check, which kept showing
    customers even after they paid "yesterday" if they had an older
    backlog within the selected window - that lifetime-cumulative concept
    is now served separately by /arrears.
    """
    from datetime import datetime as _dt

    check_date = (
        _dt.strptime(date, "%Y-%m-%d").date() if date else now_eat().date()
    )

    from sqlalchemy.orm import selectinload

    loans = db.query(Loan).options(selectinload(Loan.customer)).filter(
        Loan.status != LoanStatus.COMPLETED,
        Loan.remaining_amount > 0,
    ).all()

    eligible = []
    for loan in loans:
        start = loan.start_date.date() if isinstance(loan.start_date, _dt) else loan.start_date
        if not start or check_date < start:
            continue
        last_day = start + timedelta(days=29)
        if check_date > last_day:
            continue
        eligible.append(loan)

    if not eligible:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    loan_ids = [loan.id for loan in eligible]
    day_installments = db.query(Installment).filter(
        Installment.loan_id.in_(loan_ids),
        func.date(Installment.payment_date) == check_date,
    ).all()

    paid_today_by_loan = {}
    for inst in day_installments:
        paid_today_by_loan[inst.loan_id] = paid_today_by_loan.get(inst.loan_id, 0.0) + float(inst.amount or 0)

    items = []
    for loan in eligible:
        paid_today = paid_today_by_loan.get(loan.id, 0.0)
        if paid_today >= loan.daily_instalment - 0.01:
            continue  # fully paid for this date

        customer = loan.customer
        items.append({
            "loan_id": loan.id,
            "customer_name": customer.name if customer else None,
            "customer_phone": customer.phone if customer else None,
            "customer_id_number": loan.customer_id,
            "daily_instalment": loan.daily_instalment,
            "loan_balance": float(loan.remaining_amount if loan.remaining_amount is not None else loan.total_amount),
            "start_date": str(loan.start_date.date() if isinstance(loan.start_date, _dt) else loan.start_date),
            "due_date": str(loan.due_date) if loan.due_date else None,
        })

    total = len(items)
    page = items[offset:offset + limit]

    return {
        "items": page,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/uncollected-dues-report")
def get_uncollected_dues_report(
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    PDF report of uncollected dues for the given date.

    Accepts either a single `date` (matching the single-day model used by
    the on-screen list at /uncollected-dues) or an explicit `start_date` /
    `end_date` range for backward compatibility.
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

    if not start_date or not end_date:
        effective_date = date or now_eat().date().isoformat()
        start_date = start_date or effective_date
        end_date = end_date or effective_date

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
        def _fmt_arrears(val):
            v = float(val)
            if v > 0.01:
                return f"+{v:,.2f}"
            elif v < -0.01:
                return f"-{abs(v):,.2f}"
            else:
                return "0.00"

        rows = [["#", "CUSTOMER", "PHONE", "DAILY INSTALMENT (KES)", "LOAN BALANCE (KES)", "SKIPPED DAYS", "ARREARS (KES)"]]
        for idx, row in enumerate(items, 1):
            rows.append([
                str(idx),
                row["customer_name"] or "-",
                row["customer_phone"] or "-",
                f"{float(row['daily_instalment']):,.2f}",
                f"{float(row['loan_balance']):,.2f}",
                str(row["skipped_days"]),
                _fmt_arrears(row.get("arrears", 0)),
            ])

        tbl = Table(rows, repeatRows=1,
                    colWidths=[7*mm, 36*mm, 26*mm, 28*mm, 28*mm, 20*mm, 26*mm])
        tbl.setStyle(TableStyle([
            ("FONTNAME",       (0,0),(-1, 0), "Helvetica-Bold"),
            ("FONTNAME",       (0,1),(-1,-1), "Helvetica"),
            ("FONTSIZE",       (0,0),(-1,-1), 7.5),
            ("TEXTCOLOR",      (0,0),(-1, 0), SLATE),
            ("BACKGROUND",     (0,0),(-1, 0), LIGHT_BG),
            ("ALIGN",          (0,0),(0,-1),  "CENTER"),
            ("ALIGN",          (1,0),(2,-1),  "LEFT"),
            ("ALIGN",          (3,0),(4,-1),  "RIGHT"),
            ("ALIGN",          (6,0),(6,-1),  "RIGHT"),
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

@router.get("/payments-report")
def get_payments_report(
    date_str: str,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    PDF report of all payments received on a given date.
    date_str: YYYY-MM-DD
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

    try:
        target_date = _dt.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    EAT = ZoneInfo("Africa/Nairobi")
    day_start = _dt.combine(target_date, _dt.min.time(), tzinfo=EAT)
    day_end   = _dt.combine(target_date, _dt.max.time(), tzinfo=EAT)

    installments = (
        db.query(Installment)
        .options(
            selectinload(Installment.loan).selectinload(Loan.customer)
        )
        .filter(
            Installment.payment_date >= day_start,
            Installment.payment_date <= day_end,
        )
        .order_by(Installment.payment_date.desc())
        .all()
    )

    total_collected = sum(float(i.amount or 0) for i in installments)

    # ---- PDF ----
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

    inst_style   = ParagraphStyle("Inst",  parent=base["Normal"], fontName="Helvetica-Bold", fontSize=17, textColor=NAVY, leading=20)
    tag_style    = ParagraphStyle("Tag",   parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8, textColor=GOLD, leading=10)
    rt_style     = ParagraphStyle("RT",    parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9, textColor=NAVY, leading=11, alignment=TA_RIGHT)
    rs_style     = ParagraphStyle("RS",    parent=base["Normal"], fontName="Helvetica", fontSize=8, textColor=SLATE, leading=10, alignment=TA_RIGHT)
    label_style  = ParagraphStyle("Lbl",   parent=base["Normal"], fontName="Helvetica", fontSize=7.5, textColor=SLATE, leading=10)
    sl_style     = ParagraphStyle("SL",    parent=base["Normal"], fontName="Helvetica", fontSize=7.5, textColor=SLATE, leading=10, alignment=TA_CENTER)
    sv_style     = ParagraphStyle("SV",    parent=base["Normal"], fontName="Helvetica-Bold", fontSize=13, textColor=NAVY, leading=16, alignment=TA_CENTER)
    footer_style = ParagraphStyle("Ftr",   parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=7, textColor=SLATE, leading=10, alignment=TA_CENTER)

    story = []

    # Letterhead
    left_tbl = Table(
        [[Paragraph("KODONGO SAVINGS & CREDIT", inst_style)],
         [Paragraph("Trusted Financial Solutions", tag_style)]],
        colWidths=[None],
    )
    left_tbl.setStyle(TableStyle([
        ("LEFTPADDING",   (0,0),(-1,-1), 0),
        ("RIGHTPADDING",  (0,0),(-1,-1), 0),
        ("TOPPADDING",    (0,0),(-1,-1), 0),
        ("BOTTOMPADDING", (0,0),(-1,-1), 2),
    ]))
    right_tbl = Table(
        [[Paragraph("PAYMENTS REPORT", rt_style)],
         [Paragraph(f"Date: {target_date.strftime('%d %b %Y')}", rs_style)],
         [Paragraph(f"Generated: {_dt.now(ZoneInfo('Africa/Nairobi')).strftime('%d %b %Y, %H:%M')} EAT", rs_style)]],
        colWidths=[None],
    )
    right_tbl.setStyle(TableStyle([
        ("LEFTPADDING",   (0,0),(-1,-1), 0),
        ("RIGHTPADDING",  (0,0),(-1,-1), 0),
        ("TOPPADDING",    (0,0),(-1,-1), 0),
        ("BOTTOMPADDING", (0,0),(-1,-1), 2),
    ]))
    hdr = Table([[left_tbl, right_tbl]], colWidths=["60%","40%"])
    hdr.setStyle(TableStyle([
        ("VALIGN",       (0,0),(-1,-1), "TOP"),
        ("LEFTPADDING",  (0,0),(-1,-1), 0),
        ("RIGHTPADDING", (0,0),(-1,-1), 0),
    ]))
    story.append(hdr)
    story.append(Spacer(1, 5))
    story.append(HRFlowable(width="100%", thickness=2.5, color=NAVY, spaceAfter=2))
    story.append(HRFlowable(width="100%", thickness=1,   color=GOLD, spaceAfter=10))

    # Summary strip
    summary_rows = [
        [Paragraph("TOTAL PAYMENTS", sl_style),
         Paragraph("TOTAL COLLECTED", sl_style)],
        [Paragraph(str(len(installments)), sv_style),
         Paragraph(f"KES {total_collected:,.2f}", sv_style)],
    ]
    sum_tbl = Table(summary_rows, colWidths=["30%","70%"])
    sum_tbl.setStyle(TableStyle([
        ("BOX",           (0,0),(-1,-1), 0.75, BORDER),
        ("LINEAFTER",     (0,0),(0,-1),  0.5,  BORDER),
        ("TOPPADDING",    (0,0),(-1,-1), 6),
        ("BOTTOMPADDING", (0,0),(-1,-1), 6),
    ]))
    story.append(sum_tbl)
    story.append(Spacer(1, 14))

    # Payments table
    if not installments:
        story.append(Paragraph("No payments recorded for this date.", base["Normal"]))
    else:
        rows = [["#", "CUSTOMER", "ID", "PHONE", "AMOUNT (KES)", "TIME", "RECORDED BY", "BALANCE (KES)"]]
        for idx, inst in enumerate(installments, 1):
            loan     = inst.loan
            customer = loan.customer if loan else None
            time_str = inst.payment_date.strftime("%H:%M") if inst.payment_date else "-"
            recorded_by = (inst.recorded_by or "").strip() or "System"
            _bal = inst.balance_after if (hasattr(inst, 'balance_after') and inst.balance_after is not None) else (loan.remaining_amount if loan else None)
            balance = f"{float(_bal):,.2f}" if _bal is not None else "-"
            rows.append([
                str(idx),
                customer.name if customer else "-",
                customer.id_number if customer else "-",
                customer.phone if customer else "-",
                f"{float(inst.amount):,.2f}",
                time_str,
                recorded_by,
                balance,
            ])

        tbl = Table(rows, repeatRows=1,
                    colWidths=[8*mm, 38*mm, 22*mm, 28*mm, 25*mm, 15*mm, 25*mm, 25*mm])
        tbl.setStyle(TableStyle([
            ("FONTNAME",       (0,0),(-1, 0), "Helvetica-Bold"),
            ("FONTNAME",       (0,1),(-1,-1), "Helvetica"),
            ("FONTSIZE",       (0,0),(-1,-1), 7.5),
            ("TEXTCOLOR",      (0,0),(-1, 0), SLATE),
            ("BACKGROUND",     (0,0),(-1, 0), LIGHT_BG),
            ("ALIGN",          (4,0),(4,-1),  "RIGHT"),
            ("ALIGN",          (7,0),(7,-1),  "RIGHT"),
            ("ALIGN",          (0,0),(0,-1),  "CENTER"),
            ("ALIGN",          (1,0),(3,-1),  "LEFT"),
            ("ALIGN",          (5,0),(6,-1),  "CENTER"),
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



    # Footer
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
        headers={"Content-Disposition": f"attachment; filename=payments_report_{date_str}.pdf"},
    )



@router.get("/cleared-loans-report")
def get_cleared_loans_report(
    period: str = None,
    start_date: str = None,
    end_date: str = None,
    q: str = None,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    PDF report of all cleared loans (remaining_amount = 0).
    Supports period shortcuts or custom start_date/end_date.
    """
    from io import BytesIO
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    from datetime import date as _date, timedelta
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

    today = _date.today()

    if period and period != "custom":
        if period == "today":
            d_start = d_end = today
        elif period == "this_week":
            d_start = today - timedelta(days=today.weekday())
            d_end = today
        elif period == "this_month":
            d_start = today.replace(day=1)
            d_end = today
        elif period == "this_year":
            d_start = today.replace(month=1, day=1)
            d_end = today
        else:
            d_start = d_end = today
    else:
        try:
            d_start = _dt.strptime(start_date, "%Y-%m-%d").date() if start_date else today
            d_end   = _dt.strptime(end_date,   "%Y-%m-%d").date() if end_date   else today
        except ValueError:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    dt_start = _dt.combine(d_start, _dt.min.time())
    dt_end   = _dt.combine(d_end,   _dt.max.time())

    query = (
        db.query(Loan)
        .options(selectinload(Loan.customer))
        .filter(Loan.remaining_amount == 0)
        .filter(Loan.completed_at >= dt_start, Loan.completed_at <= dt_end)
    )
    if q and q.strip():
        search = f"%{q.strip()}%"
        from app.models import Customer
        query = query.join(Loan.customer).filter(
            (Customer.name.ilike(search)) |
            (Customer.id_number.ilike(search)) |
            (Customer.phone.ilike(search))
        )
    loans = query.order_by(Loan.completed_at.desc()).all()

    total_cleared = sum(float(loan.total_amount or 0) for loan in loans)

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

    inst_style   = ParagraphStyle("CL_Inst",  parent=base["Normal"], fontName="Helvetica-Bold",    fontSize=17, textColor=NAVY,  leading=20)
    tag_style    = ParagraphStyle("CL_Tag",   parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8,  textColor=GOLD,  leading=10)
    rt_style     = ParagraphStyle("CL_RT",    parent=base["Normal"], fontName="Helvetica-Bold",    fontSize=9,  textColor=NAVY,  leading=11, alignment=TA_RIGHT)
    rs_style     = ParagraphStyle("CL_RS",    parent=base["Normal"], fontName="Helvetica",         fontSize=8,  textColor=SLATE, leading=10, alignment=TA_RIGHT)
    sl_style     = ParagraphStyle("CL_SL",    parent=base["Normal"], fontName="Helvetica",         fontSize=7.5,textColor=SLATE, leading=10, alignment=TA_CENTER)
    sv_style     = ParagraphStyle("CL_SV",    parent=base["Normal"], fontName="Helvetica-Bold",    fontSize=13, textColor=NAVY,  leading=16, alignment=TA_CENTER)
    footer_style = ParagraphStyle("CL_Ftr",   parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=7,  textColor=SLATE, leading=10, alignment=TA_CENTER)

    story = []

    date_label = d_start.strftime("%d %b %Y") if d_start == d_end else f"{d_start.strftime('%d %b %Y')} - {d_end.strftime('%d %b %Y')}"
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
        [[Paragraph("CLEARED LOANS REPORT", rt_style)],
         [Paragraph(f"Period: {date_label}", rs_style)],
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
        [[Paragraph("TOTAL CLEARED", sl_style), Paragraph("TOTAL AMOUNT CLEARED", sl_style)],
         [Paragraph(str(len(loans)), sv_style),  Paragraph(f"KES {total_cleared:,.2f}", sv_style)]],
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

    if not loans:
        story.append(Paragraph("No cleared loans found for this period.", base["Normal"]))
    else:
        rows = [["#", "CUSTOMER", "ID NUMBER", "PHONE", "DATE CREATED", "TOTAL PAID (KES)", "CLEARED DATE"]]
        for idx, loan in enumerate(loans, 1):
            customer = loan.customer
            cleared_date = loan.completed_at.strftime("%d %b %Y") if loan.completed_at else "-"
            date_created = loan.start_date.strftime("%d %b %Y") if loan.start_date else "-"
            total_paid = float(loan.total_amount or 0) - float(loan.remaining_amount or 0)
            rows.append([
                str(idx),
                customer.name      if customer else "-",
                customer.id_number if customer else "-",
                customer.phone     if customer else "-",
                date_created,
                f"{total_paid:,.2f}",
                cleared_date,
            ])

        tbl = Table(rows, repeatRows=1,
                    colWidths=[8*mm, 42*mm, 28*mm, 30*mm, 32*mm, 32*mm, 24*mm])
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
    suffix = d_start.isoformat() if d_start == d_end else f"{d_start.isoformat()}_{d_end.isoformat()}"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=cleared_loans_report_{suffix}.pdf"},
    )







# ─── Disbursed loans PDF report ─────────────────────────────────────────
@router.get("/disbursed-loans-report")
def get_disbursed_loans_report(
    start_date: str = None,
    end_date: str = None,
    q: str = None,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    PDF report of loans disbursed within the given date range (filtered by start_date).
    """
    from io import BytesIO
    from datetime import datetime as _dt, date as _date
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

    today = _date.today()
    try:
        d_start = _dt.strptime(start_date, "%Y-%m-%d").date() if start_date else today
        d_end   = _dt.strptime(end_date,   "%Y-%m-%d").date() if end_date   else today
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    query = (
        db.query(Loan)
        .options(selectinload(Loan.customer))
        .filter(Loan.start_date >= d_start, Loan.start_date <= d_end)
    )
    if q and q.strip():
        search = f"%{q.strip()}%"
        query = query.join(Loan.customer).filter(
            (Customer.name.ilike(search)) |
            (Customer.id_number.ilike(search)) |
            (Customer.phone.ilike(search))
        )
    loans = query.order_by(Loan.start_date.desc()).all()

    total_disbursed = sum(float(loan.amount or 0) for loan in loans)

    # ── PDF ──────────────────────────────────────────────────────────────
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

    inst_style   = ParagraphStyle("DL_Inst",  parent=base["Normal"], fontName="Helvetica-Bold",    fontSize=17, textColor=NAVY,  leading=20)
    tag_style    = ParagraphStyle("DL_Tag",   parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8,  textColor=GOLD,  leading=10)
    rt_style     = ParagraphStyle("DL_RT",    parent=base["Normal"], fontName="Helvetica-Bold",    fontSize=9,  textColor=NAVY,  leading=11, alignment=TA_RIGHT)
    rs_style     = ParagraphStyle("DL_RS",    parent=base["Normal"], fontName="Helvetica",         fontSize=8,  textColor=SLATE, leading=10, alignment=TA_RIGHT)
    sl_style     = ParagraphStyle("DL_SL",    parent=base["Normal"], fontName="Helvetica",         fontSize=7.5,textColor=SLATE, leading=10, alignment=TA_CENTER)
    sv_style     = ParagraphStyle("DL_SV",    parent=base["Normal"], fontName="Helvetica-Bold",    fontSize=13, textColor=NAVY,  leading=16, alignment=TA_CENTER)
    footer_style = ParagraphStyle("DL_Ftr",   parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=7,  textColor=SLATE, leading=10, alignment=TA_CENTER)

    story = []

    date_label = (
        d_start.strftime("%d %b %Y")
        if d_start == d_end
        else f"{d_start.strftime('%d %b %Y')} - {d_end.strftime('%d %b %Y')}"
    )

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
        [[Paragraph("DISBURSED LOANS REPORT", rt_style)],
         [Paragraph(f"Period: {date_label}", rs_style)],
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
        [[Paragraph("TOTAL DISBURSED", sl_style), Paragraph("TOTAL AMOUNT (KES)", sl_style)],
         [Paragraph(str(len(loans)), sv_style), Paragraph(f"KES {total_disbursed:,.2f}", sv_style)]],
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

    if not loans:
        story.append(Paragraph("No loans disbursed in this period.", base["Normal"]))
    else:
        rows = [["#", "CUSTOMER", "ID NUMBER", "PHONE", "AMOUNT (KES)", "TOTAL + INTEREST (KES)", "DATE DISBURSED", "DUE DATE"]]
        for idx, loan in enumerate(loans, 1):
            c = loan.customer
            rows.append([
                str(idx),
                c.name if c else "-",
                c.id_number if c else "-",
                c.phone if c else "-",
                f"{float(loan.amount or 0):,.2f}",
                f"{float(loan.total_amount or 0):,.2f}",
                loan.start_date.strftime("%d %b %Y") if loan.start_date else "-",
                loan.due_date.strftime("%d %b %Y") if loan.due_date else "-",
            ])

        tbl = Table(rows, repeatRows=1,
                    colWidths=[8*mm, 36*mm, 24*mm, 26*mm, 24*mm, 28*mm, 24*mm, 24*mm])
        tbl.setStyle(TableStyle([
            ("FONTNAME",       (0,0),(-1, 0), "Helvetica-Bold"),
            ("FONTNAME",       (0,1),(-1,-1), "Helvetica"),
            ("FONTSIZE",       (0,0),(-1,-1), 7.5),
            ("TEXTCOLOR",      (0,0),(-1, 0), SLATE),
            ("BACKGROUND",     (0,0),(-1, 0), LIGHT_BG),
            ("ALIGN",          (0,0),(0,-1),  "CENTER"),
            ("ALIGN",          (1,0),(3,-1),  "LEFT"),
            ("ALIGN",          (4,0),(5,-1),  "RIGHT"),
            ("ALIGN",          (6,0),(7,-1),  "CENTER"),
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
    suffix = d_start.isoformat() if d_start == d_end else f"{d_start.isoformat()}_{d_end.isoformat()}"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=disbursed_loans_report_{suffix}.pdf"},
    )


# ============ ARREARS (lifetime backlog, any status except COMPLETED) ============

def _calc_arrears(db: Session):
    """
    ARREARS = lifetime cumulative backlog on any non-completed loan
    (ACTIVE, OVERDUE, or flagged defaulter - anyone with balance > 0).

    Unlike the old date-range "Uncollected Dues" window logic, this is NOT
    forgiving across a window - it looks at the loan's ENTIRE history from
    start_date to today. A loan only disappears from Arrears once its
    cumulative paid catches back up to cumulative expected.

    Returns per loan: backlog amount, total loan amount, remaining balance,
    start date, due date, and the full list of skipped dates (for expand
    on click in the UI) - not just a count.
    """
    from sqlalchemy.orm import selectinload
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    from collections import defaultdict as _defaultdict

    today = now_eat().date()

    loans = db.query(Loan).options(selectinload(Loan.customer)).filter(
        Loan.status != LoanStatus.COMPLETED,
        Loan.remaining_amount > 0,
    ).all()

    if not loans:
        return []

    loan_ids = [loan.id for loan in loans]
    all_installments = db.query(Installment).filter(
        Installment.loan_id.in_(loan_ids),
        func.date(Installment.payment_date) <= today,
    ).all()

    sums_by_loan = _defaultdict(lambda: _defaultdict(float))
    for inst in all_installments:
        d = inst.payment_date.date() if isinstance(inst.payment_date, _dt) else inst.payment_date
        sums_by_loan[inst.loan_id][d] += float(inst.amount or 0)

    items = []
    for loan in loans:
        start = loan.start_date.date() if isinstance(loan.start_date, _dt) else loan.start_date
        if not start or today < start:
            continue

        daily_instalment = loan.daily_instalment
        sums_by_date = sums_by_loan[loan.id]

        due = loan.due_date.date() if isinstance(loan.due_date, _dt) else loan.due_date

        # Cap elapsed days at the loan's actual term (start -> due date),
        # so a loan that's months old doesn't keep accumulating expected
        # instalments past its own schedule.
        term_days = (due - start).days + 1 if due else None
        elapsed_days = (today - start).days + 1
        if term_days:
            elapsed_days = min(elapsed_days, term_days)

        expected_total = daily_instalment * elapsed_days
        # Safety net: expected can never exceed the total loan amount.
        expected_total = min(expected_total, loan.total_amount)

        paid_total_installments = sum(sums_by_date.values())
        # Authoritative paid amount, from the loan's own balance bookkeeping.
        # Covers payments that updated remaining_amount without leaving a
        # matching Installment row (e.g. some M-Pesa callback paths).
        paid_total_balance = loan.total_amount - loan.remaining_amount
        paid_total = max(paid_total_installments, paid_total_balance)

        backlog = expected_total - paid_total
        # Backlog can never exceed what the customer actually still owes -
        # there are no penalties and the loan balance itself never grows.
        backlog = min(backlog, loan.remaining_amount)

        if backlog <= 0.01:
            continue  # not behind, lifetime-cumulative

        # Every day within the loan's term where paid < daily instalment -
        # not just a trailing consecutive run, so a recent payment doesn't
        # mask an older unpaid gap.
        # Running credit balance: a day only counts as genuinely skipped if
        # cumulative payments to date fall short of cumulative instalments
        # due to date. This lets a prepayment (or lump sum) roll forward and
        # cover future days, instead of flagging a day just because nothing
        # landed on that exact date.
        end_day = min(today, due) if due else today
        skipped_dates = []
        running_balance = 0.0
        current = start
        while current <= end_day:
            paid = sums_by_date.get(current, 0.0)
            running_balance += paid - daily_instalment
            if running_balance < -0.01:
                skipped_dates.append(str(current))
            current += _td(days=1)

        customer = loan.customer
        items.append({
            "loan_id": loan.id,
            "customer_id": loan.customer_id,
            "customer_name": customer.name if customer else None,
            "customer_phone": customer.phone if customer else None,
            "customer_id_number": loan.customer_id,
            "backlog_amount": backlog,
            "total_loan_amount": loan.total_amount,
            "remaining_balance": loan.remaining_amount,
            "start_date": str(start),
            "due_date": str(loan.due_date) if loan.due_date else None,
            "skipped_days_count": len(skipped_dates),
            "skipped_dates": skipped_dates,
        })

    items.sort(key=lambda r: r["backlog_amount"], reverse=True)
    return items


@router.get("/arrears")
def get_arrears_list(
    limit: int = 50,
    offset: int = 0,
    q: str = None,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """Get all customers with a lifetime cumulative payment backlog."""
    items = _calc_arrears(db)

    if q:
        needle = q.strip().lower()
        items = [
            r for r in items
            if needle in (r.get("customer_name") or "").lower()
            or needle in (r.get("customer_phone") or "").lower()
            or needle in (r.get("customer_id_number") or "").lower()
        ]

    total = len(items)
    page = items[offset:offset + limit]
    return {
        "items": page,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/arrears-report")
def get_arrears_report(
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """PDF report of all arrears (lifetime backlog)."""
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

    items = _calc_arrears(db)
    total_backlog = sum(float(row["backlog_amount"] or 0) for row in items)

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

    inst_style = ParagraphStyle("AR_Inst", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=17, textColor=NAVY, leading=20)
    tag_style  = ParagraphStyle("AR_Tag",  parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8, textColor=GOLD, leading=10)
    rt_style   = ParagraphStyle("AR_RT",   parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9, textColor=NAVY, leading=11, alignment=TA_RIGHT)
    rs_style   = ParagraphStyle("AR_RS",   parent=base["Normal"], fontName="Helvetica", fontSize=8, textColor=SLATE, leading=10, alignment=TA_RIGHT)
    sl_style   = ParagraphStyle("AR_SL",   parent=base["Normal"], fontName="Helvetica", fontSize=7.5, textColor=SLATE, leading=10, alignment=TA_CENTER)
    sv_style   = ParagraphStyle("AR_SV",   parent=base["Normal"], fontName="Helvetica-Bold", fontSize=13, textColor=NAVY, leading=16, alignment=TA_CENTER)

    story = []
    left_tbl = Table(
        [[Paragraph("KODONGO SAVINGS & CREDIT", inst_style)],
         [Paragraph("Trusted Financial Solutions", tag_style)]],
        colWidths=[None],
    )
    left_tbl.setStyle(TableStyle([
        ("LEFTPADDING", (0,0),(-1,-1), 0), ("RIGHTPADDING", (0,0),(-1,-1), 0),
        ("TOPPADDING", (0,0),(-1,-1), 0), ("BOTTOMPADDING", (0,0),(-1,-1), 2),
    ]))
    right_tbl = Table(
        [[Paragraph("ARREARS REPORT", rt_style)],
         [Paragraph(f"Generated: {_dt.now(ZoneInfo('Africa/Nairobi')).strftime('%d %b %Y, %H:%M')} EAT", rs_style)]],
        colWidths=[None],
    )
    right_tbl.setStyle(TableStyle([
        ("LEFTPADDING", (0,0),(-1,-1), 0), ("RIGHTPADDING", (0,0),(-1,-1), 0),
        ("TOPPADDING", (0,0),(-1,-1), 0), ("BOTTOMPADDING", (0,0),(-1,-1), 2),
    ]))
    hdr = Table([[left_tbl, right_tbl]], colWidths=["60%","40%"])
    hdr.setStyle(TableStyle([
        ("VALIGN", (0,0),(-1,-1), "TOP"),
        ("LEFTPADDING", (0,0),(-1,-1), 0), ("RIGHTPADDING", (0,0),(-1,-1), 0),
    ]))
    story.append(hdr)
    story.append(Spacer(1, 5))
    story.append(HRFlowable(width="100%", thickness=2.5, color=NAVY, spaceAfter=2))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD, spaceAfter=10))

    sum_tbl = Table(
        [[Paragraph("TOTAL CUSTOMERS", sl_style), Paragraph("TOTAL BACKLOG (KES)", sl_style)],
         [Paragraph(str(len(items)), sv_style), Paragraph(f"KES {total_backlog:,.2f}", sv_style)]],
        colWidths=["30%", "70%"],
    )
    sum_tbl.setStyle(TableStyle([
        ("BOX", (0,0),(-1,-1), 0.75, BORDER),
        ("LINEAFTER", (0,0),(0,-1), 0.5, BORDER),
        ("TOPPADDING", (0,0),(-1,-1), 6),
        ("BOTTOMPADDING", (0,0),(-1,-1), 6),
    ]))
    story.append(sum_tbl)
    story.append(Spacer(1, 14))

    if not items:
        story.append(Paragraph("No customers currently in arrears.", base["Normal"]))
    else:
        rows = [["#", "CUSTOMER", "PHONE", "TOTAL LOAN (KES)", "BALANCE (KES)", "SKIPPED", "BACKLOG (KES)"]]
        for idx, row in enumerate(items, 1):
            rows.append([
                str(idx),
                row["customer_name"] or "-",
                row["customer_phone"] or "-",
                f"{float(row['total_loan_amount']):,.2f}",
                f"{float(row['remaining_balance']):,.2f}",
                str(row["skipped_days_count"]),
                f"{float(row['backlog_amount']):,.2f}",
            ])
        tbl = Table(rows, repeatRows=1, colWidths=[7*mm, 34*mm, 24*mm, 28*mm, 26*mm, 18*mm, 28*mm])
        tbl.setStyle(TableStyle([
            ("FONTNAME", (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTNAME", (0,1),(-1,-1), "Helvetica"),
            ("FONTSIZE", (0,0),(-1,-1), 7.5),
            ("TEXTCOLOR", (0,0),(-1,0), SLATE),
            ("BACKGROUND", (0,0),(-1,0), LIGHT_BG),
            ("ALIGN", (0,0),(0,-1), "CENTER"),
            ("ALIGN", (1,0),(2,-1), "LEFT"),
            ("ALIGN", (3,0),(4,-1), "RIGHT"),
            ("ALIGN", (6,0),(6,-1), "RIGHT"),
            ("ALIGN", (5,0),(5,-1), "CENTER"),
            ("LINEBELOW", (0,0),(-1,0), 0.75, BORDER),
            ("LINEBELOW", (0,1),(-1,-2), 0.35, BORDER),
            ("BOX", (0,0),(-1,-1), 0.75, BORDER),
            ("ROWBACKGROUNDS", (0,1),(-1,-1), [colors.white, LIGHT_BG]),
            ("TOPPADDING", (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("LEFTPADDING", (0,0),(-1,-1), 5),
            ("RIGHTPADDING", (0,0),(-1,-1), 5),
        ]))
        story.append(tbl)

    story.append(Spacer(1, 18))
    story.append(HRFlowable(width="100%", thickness=0.75, color=BORDER, spaceAfter=6))
    story.append(Paragraph(
        f"Generated on {_dt.now(ZoneInfo('Africa/Nairobi')).strftime('%d %B %Y at %H:%M EAT')}. "
        f"This report is for internal use only. Kodongo Savings & Credit.",
        ParagraphStyle("AR_Ftr", parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=7, textColor=SLATE, leading=10, alignment=TA_CENTER),
    ))

    doc.build(story)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=arrears_report_{now_eat().strftime('%Y%m%d')}.pdf"},
    )

# === ARREARS_FEATURE_APPLIED ===
