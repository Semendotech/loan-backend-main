"""Delete customers whose phone numbers fail validation (too short/long/invalid)."""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import delete, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import AsyncSessionLocal
from app.models import Arrears, Customer, Installment, Loan, MpesaTransaction
from app.utils.phone import normalize_phone


def is_valid_phone(phone: str | None) -> bool:
    if not phone:
        return False
    try:
        normalize_phone(phone)
        return True
    except ValueError:
        return False


async def delete_invalid_phone_customers() -> None:
    deleted_customers = 0
    deleted_loans = 0

    async with AsyncSessionLocal() as session:
        customers = (await session.execute(select(Customer))).scalars().all()
        invalid_customers = [c for c in customers if not is_valid_phone(c.phone)]

        if not invalid_customers:
            print("No customers with invalid phone numbers found.")
            return

        print(f"Found {len(invalid_customers)} customers with invalid phone numbers:\n")
        for customer in invalid_customers:
            print(f"  - id={customer.id} name={customer.name!r} phone={customer.phone!r}")

        for customer in invalid_customers:
            loans = (
                await session.execute(
                    select(Loan).where(Loan.customer_id == customer.id_number)
                )
            ).scalars().all()
            loan_ids = [loan.id for loan in loans]

            if loan_ids:
                await session.execute(
                    delete(Installment).where(Installment.loan_id.in_(loan_ids))
                )
                await session.execute(
                    delete(Arrears).where(Arrears.loan_id.in_(loan_ids))
                )
                await session.execute(
                    delete(MpesaTransaction).where(MpesaTransaction.loan_id.in_(loan_ids))
                )
                await session.execute(delete(Loan).where(Loan.id.in_(loan_ids)))
                deleted_loans += len(loan_ids)

            await session.execute(
                delete(Arrears).where(Arrears.customer_id == customer.id)
            )
            await session.execute(delete(Customer).where(Customer.id == customer.id))
            deleted_customers += 1

        await session.commit()

    print(
        f"\nDeleted {deleted_customers} customers and {deleted_loans} related loan(s) "
        f"with their installments, arrears, and M-Pesa records."
    )


if __name__ == "__main__":
    asyncio.run(delete_invalid_phone_customers())
