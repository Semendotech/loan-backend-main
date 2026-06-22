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

    Implementation notes:
    - Only consider loans with remaining_amount > 0 and within 30 days since start_date.
    - Evaluate every 5-day rolling window (ending on each day since start_date)
      and flag a loan if any window has either:
        a) zero payments in that 5-day window (5 consecutive missed days), or
        b) sum(payments in that 5-day window) < 5 * daily_instalment.

    The returned dict keeps the `days_defaulted` key for compatibility. For the
    shortfall case we return the shortfall amount (rounded) in `days_defaulted`.
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

        # Determine the last day to evaluate (cannot go beyond the 30-day window)
        last_day = min(today, loan.start_date + timedelta(days=29))

        flagged = False
        flagged_value = 0

        # Precompute set of payment dates for fast membership checks
        payment_dates = set(payments_sum_by_loan_date.get(loan.id, {}).keys())

        # Evaluate every 5-day rolling window ending on each day >= start_date+4
        window_end = loan.start_date + timedelta(days=4)
        while window_end <= last_day:
            window_start = window_end - timedelta(days=4)

            # Sum payments inside window
            window_sum = 0.0
            sums_map = payments_sum_by_loan_date.get(loan.id, {})
            current = window_start
            while current <= window_end:
                window_sum += sums_map.get(current, 0.0)
                current += timedelta(days=1)

            # Check zero payments (5 consecutive missed days)
            if window_sum == 0.0:
                # compute consecutive missed days ending at window_end (may be >5)
                consec = 0
                scan = window_end
                while scan >= loan.start_date:
                    if scan in payment_dates:
                        break
                    consec += 1
                    scan -= timedelta(days=1)
                flagged = True
                flagged_value = consec
                break

            # Check shortfall in this 5-day window
            required = 5 * daily_instalment
            if window_sum < required:
                shortfall = round(required - window_sum, 2)
                flagged = True
                # store shortfall amount in the same field as days_defaulted per instructions
                flagged_value = shortfall
                break

            window_end += timedelta(days=1)

        if not flagged:
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
                "days_defaulted": flagged_value,
            }
        )

    # Sort: put largest consecutive missed days or largest shortfalls first
    defaulters.sort(key=lambda row: row["days_defaulted"], reverse=True)
    return defaulters
