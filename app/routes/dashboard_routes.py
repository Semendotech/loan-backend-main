"""
CORRECTED Dashboard Routes
- Metrics based on CORRECT definitions of ACTIVE, OVERDUE, DEFAULTERS
- ACTIVE = Days 1-30 from creation
- OVERDUE = Day 31+ from creation (tracked via Arrears)
- DEFAULTERS = ACTIVE loans with 5-day payment < required amount
"""

from fastapi import APIRouter, Depends, HTTPException
from app.utils.timezone import now_eat
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta

from app.database import get_sync_db
from app.models import Loan, Arrears, Installment, LoanStatus, Customer
from app.services.loan_service import LoanService
from app.auth import get_current_user

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

    # Get metrics
    metrics = LoanService.get_loan_dashboard_metrics(db)

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

    return {
        # Frontend-expected field names
        "total_paid_today": payments_today,
        "total_paid_this_week": payments_this_week,
        "total_paid_this_month": payments_this_month,
        "completed_loans_amount_this_month": completed_amount_this_month,
        "interest_last_three_months": interest_earned,
        "total_customers": total_customers,
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
    
    Definition: ACTIVE loans with is_defaulter == true
    
    Returns: Loans flagged as defaulters with details
    """
    from sqlalchemy.orm import selectinload
    from app.models import Customer

    query = db.query(Loan).options(selectinload(Loan.customer)).filter(
        Loan.is_defaulter == True,
        Loan.status == LoanStatus.ACTIVE,
    )

    total = query.count()
    defaulters = query.order_by(Loan.defaulter_flagged_date.desc()).limit(limit).offset(offset).all()

    from datetime import datetime as _dt
    from datetime import timedelta as _td
    from app.models import Installment as _Installment
    from collections import defaultdict as _defaultdict

    today = now_eat().date()
    loan_ids = [d.id for d in defaulters]
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
                "days_defaulted": _days_defaulted(d),
                "start_date": d.start_date,
                "date_loan_taken": d.start_date,
                "status": d.status.value,
                "is_defaulter": d.is_defaulter,
                "defaulter_flagged_date": d.defaulter_flagged_date,
                "customer": ({
                    "name": d.customer.name,
                    "id_number": d.customer.id_number,
                    "phone": d.customer.phone,
                    "location": d.customer.location,
                } if d.customer else None),
            }
            for d in defaulters
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


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

    Definition: ACTIVE loans where the instalment due for the selected date
    (end_date) has not been received, i.e. no payment recorded on that date
    covering at least the loan's daily_instalment. skipped_days = number of
    consecutive days (ending at end_date, within the loan's 30-day active
    window) where the daily instalment was not fully covered.
    """
    target_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

    # Load all active/overdue loans in one query
    from sqlalchemy.orm import selectinload
    loans = db.query(Loan).options(selectinload(Loan.customer)).filter(
        Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.OVERDUE]),
    ).all()

    # Filter to loans whose active window includes target_date
    eligible = []
    for loan in loans:
        if not loan.start_date:
            continue
        start = loan.start_date.date() if isinstance(loan.start_date, datetime) else loan.start_date
        if target_date < start:
            continue
        last_day = start + timedelta(days=29)
        if target_date > last_day:
            continue
        eligible.append((loan, start))

    if not eligible:
        return []

    # Fetch ALL installments for eligible loans in a single query
    loan_ids = [loan.id for loan, _ in eligible]
    all_installments = db.query(Installment).filter(
        Installment.loan_id.in_(loan_ids),
        func.date(Installment.payment_date) <= target_date,
    ).all()

    # Group installments by (loan_id, date)
    from collections import defaultdict
    sums: dict = defaultdict(lambda: defaultdict(float))
    for inst in all_installments:
        d = inst.payment_date.date() if isinstance(inst.payment_date, datetime) else inst.payment_date
        sums[inst.loan_id][d] += float(inst.amount or 0)

    items = []
    for loan, start in eligible:
        daily_instalment = loan.daily_instalment
        sums_by_date = sums[loan.id]

        paid_on_target = sums_by_date.get(target_date, 0.0)
        if paid_on_target >= daily_instalment - 0.01:
            continue

        skipped_days = 0
        current = target_date
        while current >= start:
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
        })

    items.sort(key=lambda r: r["skipped_days"], reverse=True)
    return items


@router.get("/uncollected-dues")
def get_uncollected_dues(
    start_date: str,
    end_date: str,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Get active/overdue loans whose instalment for `end_date` has not been
    collected, with how many consecutive days they've been skipped.
    """
    items = _calc_uncollected_dues(db, start_date, end_date)
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
    start_date: str,
    end_date: str,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    PDF report of uncollected dues for the given date.
    """
    from io import BytesIO
    from fastapi.responses import StreamingResponse
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors

    items = _calc_uncollected_dues(db, start_date, end_date)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Uncollected Dues Report", styles["Title"]))
    story.append(Paragraph(f"As of: {end_date}", styles["Normal"]))
    story.append(Spacer(1, 12))

    table_data = [["Customer", "Phone", "Daily Instalment", "Loan Balance", "Skipped Days"]]
    for row in items:
        table_data.append([
            row["customer_name"] or "-",
            row["customer_phone"] or "-",
            f"{row['daily_instalment']:.2f}",
            f"{row['loan_balance']:.2f}",
            str(row["skipped_days"]),
        ])

    if len(table_data) == 1:
        story.append(Paragraph("All dues have been collected for this date.", styles["Normal"]))
    else:
        tbl = Table(table_data, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
        ]))
        story.append(tbl)

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
    from zoneinfo import ZoneInfo, date as _date, timedelta
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

