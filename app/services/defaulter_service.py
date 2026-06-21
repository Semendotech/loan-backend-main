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


def count_consecutive_missed_days(
    start_date: date,
    payment_dates: set[date],
    reference_date: date,
) -> int:
    """Count consecutive calendar days (in EAT) with no payment, ending at reference_date."""
    missed = 0
    current = reference_date
    while current >= start_date:
        if current in payment_dates:
            break
        missed += 1
        current -= timedelta(days=1)
    return missed


async def get_defaulters(
    db: AsyncSession,
    min_days: int = DEFAULT_MIN_MISSED_DAYS,
    reference_date: date | None = None,
    min_loan_start_date: date | None = None,
) -> list[dict]:
    """
    Return customers with active loans who have missed daily instalments for
    min_days or more consecutive days. Payments are read from the installments
    table (no separate transactions/payments table exists in this schema).
    """
    today = reference_date or datetime.now(EAT).date()

    active_filter = and_(
        Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.ARREARS, LoanStatus.OVERDUE]),
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

    loan_ids = [loan.id for loan in loans]
    installments_result = await db.execute(
        select(Installment.loan_id, Installment.payment_date).where(
            Installment.loan_id.in_(loan_ids)
        )
    )

    payment_dates_by_loan: dict[int, set[date]] = defaultdict(set)
    for loan_id, payment_date in installments_result.all():
        if payment_date is not None:
            payment_dates_by_loan[loan_id].add(_payment_date_in_eat(payment_date))

    defaulters: list[dict] = []
    for loan in loans:
        if not loan.start_date:
            continue

        if min_loan_start_date and loan.start_date < min_loan_start_date:
            continue

        days_defaulted = count_consecutive_missed_days(
            loan.start_date,
            payment_dates_by_loan.get(loan.id, set()),
            today,
        )
        if days_defaulted < min_days:
            continue

        remaining = loan.remaining_amount
        if remaining is None:
            remaining = loan.total_amount

        defaulters.append(
            {
                "loan_id": loan.id,
                "customer_name": loan.customer.name if loan.customer else None,
                "id_number": loan.customer_id,
                "phone": loan.customer.phone if loan.customer else None,
                "loan_amount": float(loan.amount),
                "date_loan_taken": loan.start_date.isoformat() if loan.start_date else None,
                "loan_balance": float(remaining or 0),
                "days_defaulted": days_defaulted,
            }
        )

    defaulters.sort(key=lambda row: row["days_defaulted"], reverse=True)
    return defaulters
