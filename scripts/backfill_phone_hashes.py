"""Backfill phone_hash values for existing customers."""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import AsyncSessionLocal
from app.models import Customer
from app.utils.phone import hash_phone, normalize_phone


async def backfill_phone_hashes() -> None:
    """Normalize phones and compute phone_hash for customers missing hashes."""
    backfilled = 0
    skipped = 0

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Customer).where(Customer.phone_hash.is_(None))
        )
        customers = result.scalars().all()

        for customer in customers:
            try:
                normalized_phone = normalize_phone(customer.phone)
                phone_hash = hash_phone(normalized_phone)
            except ValueError as exc:
                print(f"Skipping customer {customer.id}: {exc}")
                skipped += 1
                continue

            customer.phone = normalized_phone
            customer.phone_hash = phone_hash
            print(f"Processing {customer.phone} → {phone_hash}")
            backfilled += 1

        if backfilled:
            await session.commit()

    print(f"Backfilled {backfilled} customers")
    if skipped:
        print(f"Skipped {skipped} customers with invalid phone numbers")


if __name__ == "__main__":
    asyncio.run(backfill_phone_hashes())
