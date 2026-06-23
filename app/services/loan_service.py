from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.models import Arrears, Customer, Loan, LoanStatus

TOTAL_WEEKS = 4


def _remaining_amount(loan: Loan) -> float:
    if loan.remaining_amount is None:
        return float(loan.total_amount)
    return float(loan.remaining_amount)


def _actual_paid(loan: Loan) -> float:
    return float(loan.total_amount) - _remaining_amount(loan)


def compute_weekly_progress(
    loan: Loan,
    reference_date: Optional[date] = None,
) -> Dict[str, float | int | None]:
    """
    Calculate how much should have been paid for the current week-based schedule
    and the arrears accumulated so far.
    """
    if not loan.start_date:
        return {
            "weekly_due_amount": 0.0,
            "weeks_elapsed": 0,
            "expected_paid": 0.0,
            "actual_paid": _actual_paid(loan),
            "arrears_amount": 0.0,
        }

    ref_date = reference_date or datetime.utcnow().date()
    weekly_due_amount = round(float(loan.total_amount) / TOTAL_WEEKS, 2)

    days_elapsed = (ref_date - loan.start_date).days
    weeks_elapsed = 0
    if days_elapsed >= 0:
        weeks_elapsed = min(TOTAL_WEEKS, (days_elapsed // 7) + 1)

    expected_paid = weekly_due_amount * weeks_elapsed
    actual_paid = _actual_paid(loan)
    arrears_amount = max(0.0, round(expected_paid - actual_paid, 2))

    return {
        "weekly_due_amount": weekly_due_amount,
        "weeks_elapsed": weeks_elapsed,
        "expected_paid": round(expected_paid, 2),
        "actual_paid": round(actual_paid, 2),
        "arrears_amount": arrears_amount,
    }


async def _ensure_overdue_record(
    db: AsyncSession,
    loan: Loan,
    remaining_amount: float,
) -> bool:
    """
    Make sure we have a matching arrears/overdue row for the given loan and that it
    mirrors the current outstanding amount.
    """
    changed = False
    result = await db.execute(select(Arrears).filter(Arrears.loan_id == loan.id))
    arrears = result.scalar_one_or_none()

    if not arrears:
        # Resolve customer internal ID without touching lazy relationships
        customer_res = await db.execute(
            select(Customer).filter(Customer.id_number == loan.customer_id)
        )
        customer = customer_res.scalar_one_or_none()
        customer_id = customer.id if customer else None

        arrears = Arrears(
            loan_id=loan.id,
            customer_id=customer_id,
            original_amount=loan.total_amount,
            remaining_amount=remaining_amount,
            is_cleared=False,
        )
        db.add(arrears)
        changed = True
    else:
        if abs((arrears.remaining_amount or 0.0) - remaining_amount) > 0.01:
            arrears.remaining_amount = remaining_amount
            changed = True
        if arrears.is_cleared:
            arrears.is_cleared = False
            arrears.cleared_date = None
            changed = True

    return changed


async def sync_overdue_state(
    db: AsyncSession,
    loan: Loan,
    reference_date: Optional[date] = None,
) -> bool:
    """
    Ensure the loan status/arrears record reflects whether it is overdue.
    Returns True if any mutation occurred.
    """
    ref_date = reference_date or datetime.utcnow().date()
    remaining_amount = _remaining_amount(loan)
    changed = False

    # Normalize legacy ARREARS state to OVERDUE
    if loan.status == LoanStatus.ARREARS:
        loan.status = LoanStatus.OVERDUE
        changed = True

    # Determine overdue based on days since loan.start_date (single source of truth)
    is_overdue = False
    if loan.start_date:
        days_since_start = (ref_date - loan.start_date).days
        if days_since_start > 30 and remaining_amount > 0:
            is_overdue = True

    if is_overdue:
        if loan.status != LoanStatus.OVERDUE:
            loan.status = LoanStatus.OVERDUE
            changed = True
        changed |= await _ensure_overdue_record(db, loan, remaining_amount)
    else:
        # Loan is not overdue anymore
        if loan.status == LoanStatus.OVERDUE and remaining_amount <= 0:
            loan.status = LoanStatus.COMPLETED
            loan.completed_at = datetime.utcnow()
            changed = True
        # Clear arrears entry if needed
        result = await db.execute(select(Arrears).filter(Arrears.loan_id == loan.id))
        arrears = result.scalar_one_or_none()
        if arrears and not arrears.is_cleared and remaining_amount <= 0:
            arrears.remaining_amount = 0.0
            arrears.is_cleared = True
            arrears.cleared_date = datetime.utcnow()
            changed = True

    return changed


async def reconcile_stale_arrears(db: AsyncSession) -> bool:
    """
    Safety net: find any Arrears row still marked active (is_cleared=False)
    whose linked Loan has already been fully paid off (remaining_amount <= 0),
    and clear it. This catches cases where a loan's remaining_amount reached
    zero through a path that didn't go through arrears_routes.pay_arrears/
    clear_arrears (e.g. a direct installment elsewhere), leaving a stale
    arrears record behind that never gets cleaned up by sync_overdue_state
    (which only re-checks loans matching its own overdue query).
    Returns True if any row was changed.
    """
    result = await db.execute(
        select(Arrears)
        .options(selectinload(Arrears.loan))
        .filter(Arrears.is_cleared == False)
    )
    stale_arrears = result.scalars().all()

    changed = False
    for arrears in stale_arrears:
        loan = arrears.loan
        if not loan:
            continue
        remaining = loan.remaining_amount
        if remaining is None:
            continue
        if remaining <= 0:
            arrears.remaining_amount = 0.0
            arrears.is_cleared = True
            arrears.cleared_date = datetime.utcnow()
            if loan.status != LoanStatus.COMPLETED:
                loan.status = LoanStatus.COMPLETED
                loan.completed_at = datetime.utcnow()
            changed = True

    return changed


def loan_is_overdue_by_schedule(loan: Loan, reference_date: Optional[date] = None) -> bool:
    ref_date = reference_date or datetime.utcnow().date()
    if not loan.start_date:
        return False
    days_since_start = (ref_date - loan.start_date).days
    return days_since_start > 30 and _remaining_amount(loan) > 0