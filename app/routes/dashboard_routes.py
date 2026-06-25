import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..models import Arrears, Customer, Installment, Loan, LoanStatus
from ..auth import get_current_user
from ..services.defaulter_service import get_defaulters
from ..services.loan_pdf_service import (
    generate_defaulters_report,
    generate_overdue_report,
    generate_payments_report,
    generate_uncollected_dues_report,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
logger = logging.getLogger(__name__)

EAT = ZoneInfo("Africa/Nairobi")


@router.get("/metrics")
async def get_dashboard_metrics(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get dashboard KPI metrics."""
    try:
        # Count active loans
        active_result = await db.execute(
            select(func.count(Loan.id), func.coalesce(func.sum(Loan.remaining_amount), 0.0))
            .where(Loan.status == LoanStatus.ACTIVE)
        )
        active_count, active_outstanding = active_result.one()
        active_count = active_count or 0
        active_outstanding = float(active_outstanding or 0)

        # Count overdue/arrears
        overdue_result = await db.execute(
            select(func.count(Arrears.id), func.coalesce(func.sum(Arrears.remaining_amount), 0.0))
            .where(Arrears.is_cleared == False)
        )
        overdue_count, overdue_outstanding = overdue_result.one()
        overdue_count = overdue_count or 0
        overdue_outstanding = float(overdue_outstanding or 0)

        # Count completed loans this month
        today = datetime.now(EAT).date()
        month_start = today.replace(day=1)
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)

        completed_result = await db.execute(
            select(func.count(Loan.id), func.coalesce(func.sum(Loan.total_amount), 0.0))
            .where(
                Loan.status == LoanStatus.COMPLETED,
                Loan.completed_at >= datetime.combine(month_start, time.min),
                Loan.completed_at <= datetime.combine(month_end, time.max),
            )
        )
        completed_count, completed_amount = completed_result.one()
        completed_count = completed_count or 0
        completed_amount = float(completed_amount or 0)

        # Count total customers
        customer_result = await db.execute(select(func.count(Customer.id)))
        total_customers = customer_result.scalar() or 0

        # Get defaulters count
        defaulters = await get_defaulters(db)
        defaulters_count = len(defaulters)

        # Calculate interest for completed loans in last 3 months
        three_months_ago = today - timedelta(days=90)
        interest_result = await db.execute(
            select(func.coalesce(func.sum(Loan.total_amount - Loan.amount), 0.0))
            .where(
                Loan.status == LoanStatus.COMPLETED,
                Loan.completed_at >= datetime.combine(three_months_ago, time.min),
            )
        )
        interest_earned = float(interest_result.scalar() or 0)

        return {
            "active_loans": active_count,
            "active_loans_outstanding": active_outstanding,
            "overdue_loans": overdue_count,
            "overdue_outstanding": overdue_outstanding,
            "completed_loans_this_month": completed_count,
            "completed_amount_this_month": completed_amount,
            "total_customers": total_customers,
            "defaulters_count": defaulters_count,
            "interest_last_three_months": interest_earned,
        }
    except Exception as e:
        logger.exception("Error fetching dashboard metrics")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary")
async def get_dashboard_summary(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get dashboard summary for current period."""
    try:
        today = datetime.now(EAT).date()
        
        # Payments today
        today_result = await db.execute(
            select(func.coalesce(func.sum(Installment.amount), 0.0))
            .where(
                func.date(Installment.payment_date) == today
            )
        )
        total_paid_today = float(today_result.scalar() or 0)

        # Payments this week
        week_start = today - timedelta(days=today.weekday())
        week_result = await db.execute(
            select(func.coalesce(func.sum(Installment.amount), 0.0))
            .where(
                func.date(Installment.payment_date) >= week_start,
                func.date(Installment.payment_date) <= today,
            )
        )
        total_paid_week = float(week_result.scalar() or 0)

        # Payments this month
        month_start = today.replace(day=1)
        month_result = await db.execute(
            select(func.coalesce(func.sum(Installment.amount), 0.0))
            .where(
                func.date(Installment.payment_date) >= month_start,
                func.date(Installment.payment_date) <= today,
            )
        )
        total_paid_month = float(month_result.scalar() or 0)

        # Active loans this month
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        active_this_month = await db.execute(
            select(func.count(Loan.id))
            .where(
                Loan.status == LoanStatus.ACTIVE,
                Loan.created_at >= datetime.combine(month_start, time.min),
                Loan.created_at <= datetime.combine(month_end, time.max),
            )
        )
        active_count_this_month = active_this_month.scalar() or 0

        # Completed loans this month
        completed_this_month = await db.execute(
            select(func.count(Loan.id))
            .where(
                Loan.status == LoanStatus.COMPLETED,
                Loan.completed_at >= datetime.combine(month_start, time.min),
                Loan.completed_at <= datetime.combine(month_end, time.max),
            )
        )
        completed_count_this_month = completed_this_month.scalar() or 0

        # Arrears count (overdue not cleared)
        arrears_result = await db.execute(
            select(func.count(Arrears.id))
            .where(Arrears.is_cleared == False)
        )
        arrears_count = arrears_result.scalar() or 0

        return {
            "total_paid_today": total_paid_today,
            "total_paid_this_week": total_paid_week,
            "total_paid_this_month": total_paid_month,
            "active_loans_count_this_month": active_count_this_month,
            "completed_loans_count_this_month": completed_count_this_month,
            "arrears_count_this_month": arrears_count,
        }
    except Exception as e:
        logger.exception("Error fetching dashboard summary")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/defaulters")
async def list_defaulters(
    limit: int = Query(1000, le=10000),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get list of defaulters with pagination."""
    try:
        defaulters = await get_defaulters(db)
        paginated = defaulters[offset : offset + limit]
        
        return {
            "items": paginated,
            "total": len(defaulters),
            "limit": limit,
            "offset": offset,
            "has_more": len(defaulters) > offset + limit,
        }
    except Exception as e:
        logger.exception("Error fetching defaulters")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trends")
async def get_loan_trends(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get loan creation and completion trends."""
    try:
        today = datetime.now(EAT).date()
        start_date = today - timedelta(days=days)

        creation_result = await db.execute(
            select(func.date(Loan.created_at).label("date"), func.count(Loan.id))
            .where(func.date(Loan.created_at) >= start_date)
            .group_by(func.date(Loan.created_at))
            .order_by(func.date(Loan.created_at))
        )
        creation_data = creation_result.all()

        completion_result = await db.execute(
            select(func.date(Loan.completed_at).label("date"), func.count(Loan.id))
            .where(
                Loan.status == LoanStatus.COMPLETED,
                func.date(Loan.completed_at) >= start_date,
            )
            .group_by(func.date(Loan.completed_at))
            .order_by(func.date(Loan.completed_at))
        )
        completion_data = completion_result.all()

        return {
            "period_days": days,
            "creation_trend": [{"date": str(d[0]), "count": d[1]} for d in creation_data],
            "completion_trend": [{"date": str(d[0]), "count": d[1]} for d in completion_data],
        }
    except Exception as e:
        logger.exception("Error fetching loan trends")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/payments-report")
async def get_payments_report(
    report_date: date = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get payments with arrears column."""
    if report_date is None:
        report_date = datetime.now(EAT).date()

    try:
        payments_result = await db.execute(
            select(Installment)
            .options(selectinload(Installment.loan).selectinload(Loan.customer))
            .where(func.date(Installment.payment_date) == report_date)
            .order_by(Installment.payment_date.desc())
        )
        payments = payments_result.scalars().all()

        items = []
        total_paid = 0.0

        for payment in payments:
            loan = payment.loan
            customer = loan.customer if loan else None

            if not loan or not customer:
                continue

            daily_instalment = float(loan.total_amount) / 30.0
            arrears_amount = max(0.0, daily_instalment - float(payment.amount))

            item = {
                "installment_id": payment.id,
                "loan_id": loan.id,
                "customer_name": customer.name,
                "customer_id": customer.id_number,
                "customer_phone": customer.phone,
                "amount": float(payment.amount),
                "daily_instalment": round(daily_instalment, 2),
                "arrears": round(arrears_amount, 2),
                "payment_date": payment.payment_date,
                "recorded_by": payment.recorded_by or "System",
                "loan_balance": float(loan.remaining_amount or 0),
                "source": payment.source,
            }
            items.append(item)
            total_paid += float(payment.amount)

        return {
            "report_date": str(report_date),
            "total_payments": len(items),
            "total_amount_paid": round(total_paid, 2),
            "items": items,
        }
    except Exception as e:
        logger.exception("Error fetching payments report")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/payments-report/pdf")
async def download_payments_report(
    report_date: date = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Download payments report as PDF."""
    if report_date is None:
        report_date = datetime.now(EAT).date()

    try:
        payments_result = await db.execute(
            select(Installment)
            .options(selectinload(Installment.loan).selectinload(Loan.customer))
            .where(func.date(Installment.payment_date) == report_date)
            .order_by(Installment.payment_date.desc())
        )
        payments = payments_result.scalars().all()

        filepath, filename = generate_payments_report(payments, report_date)
        return FileResponse(filepath, media_type="application/pdf", filename=filename)
    except Exception as e:
        logger.exception("Error downloading payments report")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/overdue-report/pdf")
async def download_overdue_report(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Download overdue loans report as PDF."""
    try:
        result = await db.execute(
            select(Arrears)
            .options(selectinload(Arrears.loan), selectinload(Arrears.customer))
            .where(Arrears.is_cleared == False)
            .order_by(Arrears.arrears_date)
        )
        arrears = result.scalars().all()

        filepath, filename = generate_overdue_report(arrears)
        return FileResponse(filepath, media_type="application/pdf", filename=filename)
    except Exception as e:
        logger.exception("Error downloading overdue report")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/defaulters-report/pdf")
async def download_defaulters_report(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Download defaulters report as PDF."""
    try:
        defaulters = await get_defaulters(db)
        filepath, filename = generate_defaulters_report(defaulters)
        return FileResponse(filepath, media_type="application/pdf", filename=filename)
    except Exception as e:
        logger.exception("Error downloading defaulters report")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/uncollected-dues")
async def get_uncollected_dues(
    report_date: date = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get all unpaid daily instalments."""
    if report_date is None:
        report_date = datetime.now(EAT).date()

    try:
        loans_result = await db.execute(
            select(Loan)
            .options(selectinload(Loan.customer), selectinload(Loan.installments))
            .where(Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.OVERDUE]))
            .where(or_(Loan.remaining_amount.is_(None), Loan.remaining_amount > 0))
        )
        loans = loans_result.scalars().all()

        items = []
        total_uncollected = 0.0

        for loan in loans:
            if not loan.customer or not loan.start_date:
                continue

            days_elapsed = (report_date - loan.start_date).days + 1
            days_elapsed = min(days_elapsed, 30)

            daily_instalment = float(loan.total_amount) / 30.0
            expected_payment = daily_instalment * days_elapsed
            actual_paid = float(loan.total_amount) - float(loan.remaining_amount or 0)
            uncollected = max(0.0, expected_payment - actual_paid)

            if uncollected > 0:
                item = {
                    "loan_id": loan.id,
                    "customer_name": loan.customer.name,
                    "customer_phone": loan.customer.phone,
                    "daily_instalment": round(daily_instalment, 2),
                    "days_elapsed": days_elapsed,
                    "expected_payment": round(expected_payment, 2),
                    "actual_paid": round(actual_paid, 2),
                    "uncollected_dues": round(uncollected, 2),
                    "loan_balance": float(loan.remaining_amount or 0),
                    "status": loan.status.value,
                }
                items.append(item)
                total_uncollected += uncollected

        items.sort(key=lambda x: x["uncollected_dues"], reverse=True)

        return {
            "report_date": str(report_date),
            "total_loans_with_dues": len(items),
            "total_uncollected_dues": round(total_uncollected, 2),
            "items": items,
        }
    except Exception as e:
        logger.exception("Error fetching uncollected dues")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/uncollected-dues/pdf")
async def download_uncollected_dues_report(
    report_date: date = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Download uncollected dues report as PDF."""
    if report_date is None:
        report_date = datetime.now(EAT).date()

    try:
        loans_result = await db.execute(
            select(Loan)
            .options(selectinload(Loan.customer))
            .where(Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.OVERDUE]))
            .where(or_(Loan.remaining_amount.is_(None), Loan.remaining_amount > 0))
        )
        loans = loans_result.scalars().all()

        filepath, filename = generate_uncollected_dues_report(loans, report_date)
        return FileResponse(filepath, media_type="application/pdf", filename=filename)
    except Exception as e:
        logger.exception("Error downloading uncollected dues report")
        raise HTTPException(status_code=500, detail=str(e))
