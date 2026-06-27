"""
CORRECTED Dashboard Routes
- Metrics based on CORRECT definitions of ACTIVE, OVERDUE, DEFAULTERS
- ACTIVE = Days 1-30 from creation
- OVERDUE = Day 31+ from creation (tracked via Arrears)
- DEFAULTERS = ACTIVE loans with 5-day payment < required amount
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta

from app.database import get_sync_db
from app.models import Loan, Arrears, Installment, LoanStatus
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

    now = datetime.utcnow()
    three_months_ago = now - timedelta(days=90)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=now.weekday())
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

    return {
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
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Get trends over time.
    
    Returns:
    - Daily active loans (last 30 days)
    - Daily completions (last 30 days)
    - Daily defaulters (last 30 days)
    """
    now = datetime.utcnow()
    thirty_days_ago = now - timedelta(days=30)

    # Loans by creation date (last 30 days)
    daily_created = db.query(
        func.date(Loan.start_date).label("date"),
        func.count(Loan.id).label("count"),
    ).filter(
        Loan.start_date >= thirty_days_ago,
    ).group_by(
        func.date(Loan.start_date),
    ).all()

    # Loans completed (last 30 days)
    daily_completed = db.query(
        func.date(Loan.completed_at).label("date"),
        func.count(Loan.id).label("count"),
    ).filter(
        Loan.completed_at >= thirty_days_ago,
    ).group_by(
        func.date(Loan.completed_at),
    ).all()

    # Defaulters by flagged date (last 30 days)
    daily_defaulters = db.query(
        func.date(Loan.defaulter_flagged_date).label("date"),
        func.count(Loan.id).label("count"),
    ).filter(
        Loan.defaulter_flagged_date >= thirty_days_ago,
    ).group_by(
        func.date(Loan.defaulter_flagged_date),
    ).all()

    return {
        "daily_created": [{"date": str(d[0]), "count": d[1]} for d in daily_created],
        "daily_completed": [{"date": str(d[0]), "count": d[1]} for d in daily_completed],
        "daily_defaulters": [{"date": str(d[0]), "count": d[1]} for d in daily_defaulters],
    }


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
    query = db.query(Loan).filter(
        Loan.is_defaulter == True,
        Loan.status == LoanStatus.ACTIVE,
    )

    total = query.count()
    defaulters = query.order_by(Loan.defaulter_flagged_date.desc()).limit(limit).offset(offset).all()

    return {
        "items": [
            {
                "id": d.id,
                "customer_id": d.customer_id,
                "amount": d.amount,
                "total_amount": d.total_amount,
                "remaining_amount": d.remaining_amount,
                "daily_instalment": d.daily_instalment,
                "days_since_start": d.days_since_start,
                "status": d.status.value,
                "is_defaulter": d.is_defaulter,
                "defaulter_flagged_date": d.defaulter_flagged_date,
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
                "days_overdue": (datetime.utcnow() - a.arrears_date).days,
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

    loans = db.query(Loan).filter(
        Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.OVERDUE]),
    ).all()

    items = []
    for loan in loans:
        if not loan.start_date:
            continue
        start = loan.start_date.date() if isinstance(loan.start_date, datetime) else loan.start_date
        if target_date < start:
            continue
        # Only consider days within the 30-day active window
        last_day = min(target_date, start + timedelta(days=29))
        if target_date > last_day:
            continue

        daily_instalment = loan.daily_instalment

        installments = db.query(Installment).filter(
            Installment.loan_id == loan.id,
            func.date(Installment.payment_date) >= start,
            func.date(Installment.payment_date) <= target_date,
        ).all()

        sums_by_date = {}
        for inst in installments:
            d = inst.payment_date.date() if isinstance(inst.payment_date, datetime) else inst.payment_date
            sums_by_date[d] = sums_by_date.get(d, 0.0) + float(inst.amount or 0)

        # Was the target date's instalment covered?
        paid_on_target = sums_by_date.get(target_date, 0.0)
        if paid_on_target >= daily_instalment - 0.01:
            continue  # fully covered, not an uncollected due

        # Compute skipped_days: consecutive missed days ending at target_date
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
