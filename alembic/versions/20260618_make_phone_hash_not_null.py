"""Make phone_hash NOT NULL and ensure all customers have hashes

Revision ID: 20260618_make_phone_hash_not_null
Revises: 20260618_add_phone_hash
Create Date: 2026-06-18 12:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import hashlib

# revision identifiers, used by Alembic.
revision: str = '20260618_make_phone_hash_not_null'
down_revision = '20260618_add_phone_hash'
branch_labels = None
depends_on = None


def normalize_phone(phone: str) -> str:
    """Mirror the normalize_phone function from utils.py"""
    phone = ''.join(filter(str.isdigit, phone))
    if phone.startswith('254'):
        phone = '0' + phone[3:]
    elif not phone.startswith('0') and len(phone) == 9:
        phone = '0' + phone
    return phone


def upgrade() -> None:
    connection = op.get_bind()
    
    # Step 1: Find any customers with NULL phone_hash and backfill them
    result = connection.execute(
        sa.text("SELECT id, phone FROM customers WHERE phone_hash IS NULL AND phone IS NOT NULL ORDER BY created_at DESC")
    )
    rows = result.fetchall()
    
    seen_hashes = set()
    seen_phones = set()
    
    if rows:
        print(f"Backfilling {len(rows)} customers with NULL phone_hash...")
        for row in rows:
            customer_id, phone = row
            normalized = normalize_phone(phone)
            phone_hash = hashlib.sha256(normalized.encode()).hexdigest()
            
            # Skip if we've already seen this normalized phone (duplicate)
            if normalized in seen_phones:
                print(f"Skipping customer {customer_id}: Duplicate phone {normalized}")
                continue
            
            seen_phones.add(normalized)
            seen_hashes.add(phone_hash)
            
            connection.execute(
                sa.text("UPDATE customers SET phone_hash = :hash, phone = :phone WHERE id = :id"),
                {"hash": phone_hash, "phone": normalized, "id": customer_id}
            )
    
    # Step 2: Normalize remaining phones (should already be normalized from first migration)
    result = connection.execute(sa.text("SELECT id, phone FROM customers WHERE phone IS NOT NULL"))
    rows = result.fetchall()
    
    for row in rows:
        customer_id, phone = row
        normalized = normalize_phone(phone)
        if normalized != phone:
            # Only update if the normalized version doesn't already exist
            check = connection.execute(
                sa.text("SELECT id FROM customers WHERE phone = :phone AND id != :id"),
                {"phone": normalized, "id": customer_id}
            )
            if not check.fetchone():
                connection.execute(
                    sa.text("UPDATE customers SET phone = :phone WHERE id = :id"),
                    {"phone": normalized, "id": customer_id}
                )
    
    # Step 3: Now make phone_hash NOT NULL
    if connection.dialect.name == 'sqlite':
        print("Skipping phone_hash ALTER NULL/NOT NULL on SQLite; schema already contains phone_hash and SQLite ALTER COLUMN is limited")
    else:
        op.alter_column('customers', 'phone_hash', existing_type=sa.String(length=64), nullable=False)
    
    print("Migration complete: phone_hash update finished")


def downgrade() -> None:
    # Make phone_hash nullable again
    op.alter_column('customers', 'phone_hash', existing_type=sa.String(length=64), nullable=True)
