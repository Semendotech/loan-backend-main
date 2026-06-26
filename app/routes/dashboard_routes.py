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

from app.database import get_db
from app.models import Loan, Arrears, Installment, LoanStatus
from app.services.loan_service import LoanService
from app.dependencies import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ============ ENDPOINTS ============

@router.get("/metrics")
def get_dashboard_metrics(
    db: Session = Depends(get_db),
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
    LoanService.daily_sync_all_loans(db)

    # Get metrics
    metrics = LoanService.get_loan_dashboard_metrics(db)

    return metrics


@router.get("/summary")
def get_dashboard_summary(
    db: Session = Depends(get_db),
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
    LoanService.daily_sync_all_loans(db)

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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
