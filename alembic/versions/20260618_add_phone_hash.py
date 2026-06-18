"""add phone_hash column to customers

Revision ID: 20260618_add_phone_hash
Revises: 20260615_add_mpesa_transactions
Create Date: 2026-06-18 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import hashlib

# revision identifiers, used by Alembic.
revision: str = '20260618_add_phone_hash'
down_revision = '20260615_add_mpesa_transactions'
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
    # Add phone_hash column
    op.add_column(
        'customers',
        sa.Column('phone_hash', sa.String(length=64), nullable=True)
    )
    op.create_index(op.f('ix_customers_phone_hash'), 'customers', ['phone_hash'], unique=True)
    
    # Backfill phone_hash by hashing existing phone numbers (after normalizing)
    connection = op.get_bind()
    result = connection.execute(sa.text("SELECT id, phone FROM customers WHERE phone IS NOT NULL"))
    
    for row in result:
        customer_id, phone = row
        # IMPORTANT: Normalize phone before hashing
        normalized = normalize_phone(phone)
        phone_hash = hashlib.sha256(normalized.encode()).hexdigest()
        
        # Update both phone (normalized) and phone_hash
        connection.execute(
            sa.text("UPDATE customers SET phone = :phone, phone_hash = :hash WHERE id = :id"),
            {"phone": normalized, "hash": phone_hash, "id": customer_id}
        )


def downgrade() -> None:
    op.drop_index(op.f('ix_customers_phone_hash'), table_name='customers')
    op.drop_column('customers', 'phone_hash')
