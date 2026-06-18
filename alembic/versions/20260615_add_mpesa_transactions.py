"""add mpesa_transactions table

Revision ID: 20260615_add_mpesa_transactions
Revises: 835018742018
Create Date: 2026-06-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260615_add_mpesa_transactions'
down_revision = '835018742018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create mpesa_transactions table
    op.create_table(
        'mpesa_transactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('trans_id', sa.String(length=100), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('phone', sa.String(length=20), nullable=False),
        sa.Column('loan_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['loan_id'], ['loans.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('trans_id')
    )
    op.create_index(op.f('ix_mpesa_transactions_trans_id'), 'mpesa_transactions', ['trans_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_mpesa_transactions_trans_id'), table_name='mpesa_transactions')
    op.drop_table('mpesa_transactions')
