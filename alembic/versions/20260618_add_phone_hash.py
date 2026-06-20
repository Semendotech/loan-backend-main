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
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {column['name'] for column in inspector.get_columns('customers')}
    existing_indexes = {idx['name'] for idx in inspector.get_indexes('customers')}

    # Add phone_hash column (nullable initially) if it doesn't exist
    if 'phone_hash' not in columns:
        op.add_column(
            'customers',
            sa.Column('phone_hash', sa.String(length=64), nullable=True)
        )
    else:
        print('Skipping phone_hash creation; column already exists.')

    # Backfill phone_hash by hashing existing phone numbers (after normalizing)
    # First, drop the unique constraint or index on phone to allow temporary duplicates during normalization
    try:
        connection.execute(sa.text('ALTER TABLE customers DROP CONSTRAINT customers_phone_key'))
    except Exception:
        try:
            connection.execute(sa.text('DROP INDEX customers_phone_key'))
        except Exception:
            pass  # Constraint/index might not exist or have different name

    # Get all customers ordered by creation date (keep most recent if duplicates)
    result = connection.execute(sa.text('''
        SELECT id, phone FROM customers WHERE phone IS NOT NULL ORDER BY created_at DESC, id DESC
    '''))

    seen_normalized_phones = set()
    duplicates_to_delete = []

    for row in result:
        customer_id, phone = row
        normalized = normalize_phone(phone)
        phone_hash = hashlib.sha256(normalized.encode()).hexdigest()

        if normalized in seen_normalized_phones:
            duplicates_to_delete.append(customer_id)
            continue

        seen_normalized_phones.add(normalized)
        connection.execute(
            sa.text('UPDATE customers SET phone = :phone, phone_hash = :hash WHERE id = :id'),
            {'phone': normalized, 'hash': phone_hash, 'id': customer_id}
        )

    for customer_id in duplicates_to_delete:
        connection.execute(sa.text('DELETE FROM customers WHERE id = :id'), {'id': customer_id})

    # Re-add unique constraint/index on phone if not already present
    if 'customers_phone_key' not in existing_indexes:
        if connection.dialect.name == 'sqlite':
            connection.execute(sa.text('CREATE UNIQUE INDEX customers_phone_key ON customers(phone)'))
        else:
            connection.execute(sa.text('ALTER TABLE customers ADD CONSTRAINT customers_phone_key UNIQUE (phone)'))

    # Create unique index on phone_hash if not already present
    if op.f('ix_customers_phone_hash') not in existing_indexes:
        op.create_index(op.f('ix_customers_phone_hash'), 'customers', ['phone_hash'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_customers_phone_hash'), table_name='customers')
    op.drop_column('customers', 'phone_hash')
