from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Installment, Loan, LoanStatus

EAT = ZoneInfo("Africa/Nairobi")
DEFAULT_MIN_MISSED_DAYS = 5


def _payment_date_in_eat(payment_date: datetime) -> date:
    if payment_date.tzinfo is None:
        payment_date = payment_date.replace(tzinfo=ZoneInfo("UTC"))
    return payment_date.astimezone(EAT).date()


async def get_defaulters(
    db: AsyncSession,
    min_days: int = DEFAULT_MIN_MISSED_DAYS,
    reference_date: date | None = None,
    min_loan_start_date: date | None = None,
) -> list[dict]:
    """
    Return customers who meet the DEFAULTER definition per business rules.

    Definition: a loan is a defaulter if, as of `today`, the customer has gone
    `min_days` (default 5) or more CONSECUTIVE days without their cumulative
    daily instalment being fully covered.

    Implementation:
    - Only consider loans with remaining_amount > 0 and within the 30-day
      active window since start_date.
    - Walk day-by-day from start_date to `last_day` (today, capped at the
      30-day window), tracking running expected total vs running paid total.
      A day counts as "missed" if cumulative paid < cumulative expected as of
      that day's end. We track the LONGEST currently-open streak of missed
      days ending at `last_day` (i.e. the consecutive missed-day count as of
      today), not just the first 5-day window that happened to qualify.
    - A loan is flagged once that current streak reaches `min_days`.

    Returns `days_defaulted` = number of consecutive days currently missed
    (as of `today`), so loans further behind correctly show more days than
    loans that just crossed the threshold.
    """
    today = reference_date or datetime.now(EAT).date()

    # Fetch candidate loans (exclude completed)
    active_filter = and_(
        Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.ARREARS]),
        or_(Loan.remaining_amount.is_(None), Loan.remaining_amount > 0),
    )

    loans_result = await db.execute(
        select(Loan)
        .options(selectinload(Loan.customer))
        .where(active_filter)
    )
    loans = loans_result.scalars().all()
    if not loans:
        return []

    # Load installments (payment amounts and dates) for these loans
    loan_ids = [loan.id for loan in loans]
    installments_result = await db.execute(
        select(Installment.loan_id, Installment.payment_date, Installment.amount).where(
            Installment.loan_id.in_(loan_ids)
        )
    )

    payments_by_loan: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for loan_id, payment_date, amount in installments_result.all():
        if payment_date is None:
            continue
        pd = _payment_date_in_eat(payment_date)
        payments_by_loan[loan_id].append((pd, float(amount or 0.0)))

    # Build quick lookup of sums per date for each loan
    payments_sum_by_loan_date: dict[int, dict[date, float]] = {}
    for lid, payments in payments_by_loan.items():
        d: dict[date, float] = defaultdict(float)
        for pd, amt in payments:
            d[pd] += amt
        payments_sum_by_loan_date[lid] = d

    defaulters: list[dict] = []
    for loan in loans:
        if not loan.start_date:
            continue
        if min_loan_start_date and loan.start_date < min_loan_start_date:
            continue

        # Only consider loans still within 30-day active window
        days_since_start = (today - loan.start_date).days
        if days_since_start < 0:
            continue
        if days_since_start > 30:
            # Past 30 days -> OVERDUE by business rules, not a defaulter
            continue

        remaining = loan.remaining_amount
        if remaining is None:
            remaining = loan.total_amount
        if remaining <= 0:
            continue

        daily_instalment = float(loan.total_amount) / 30.0
        sums_map = payments_sum_by_loan_date.get(loan.id, {})

        # Determine the last day to evaluate (cannot go beyond the 30-day window)
        last_day = min(today, loan.start_date + timedelta(days=29))

        # Walk day-by-day, tracking running expected vs running paid, and the
        # length of the currently-open streak of "behind schedule" days.
        expected_total = 0.0
        paid_total = 0.0
        current_streak = 0
        current = loan.start_date
        while current <= last_day:
            expected_total += daily_instalment
            paid_total += sums_map.get(current, 0.0)
            if paid_total < expected_total - 0.01:  # small tolerance for float rounding
                current_streak += 1
            else:
                current_streak = 0
            current += timedelta(days=1)

        if current_streak < min_days:
            continue

        defaulters.append(
            {
                "loan_id": loan.id,
                "customer_name": loan.customer.name if loan.customer else None,
                "id_number": loan.customer_id,
                "phone": loan.customer.phone if loan.customer else None,
                "loan_amount": float(loan.amount or 0),
                "date_loan_taken": loan.start_date.isoformat() if loan.start_date else None,
                "loan_balance": float(remaining or 0),
                "days_defaulted": current_streak,
            }
        )

    # Sort: largest consecutive missed days first
    defaulters.sort(key=lambda row: row["days_defaulted"], reverse=True)
    return defaulters