"""add_user_role_and_payment_metadata

Revision ID: 20260620_add_user_role_and_payment_metadata
Revises: 20260619_renormalize_phone_hash_254
Create Date: 2026-06-20 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260620_add_user_role_and_payment_metadata'
down_revision = '20260619_renormalize_phone_hash_254'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('first_name', sa.String(length=50), nullable=True))
    op.add_column('users', sa.Column('role', sa.Enum('admin', 'loan_officer', name='userrole'), nullable=False, server_default='loan_officer'))

    op.add_column('installments', sa.Column('recorded_by', sa.String(length=100), nullable=True))
    op.add_column('installments', sa.Column('source', sa.String(length=30), nullable=False, server_default='manual'))


def downgrade() -> None:
    op.drop_column('installments', 'source')
    op.drop_column('installments', 'recorded_by')
    op.drop_column('users', 'role')
    op.drop_column('users', 'first_name')
    op.execute("DROP TYPE IF EXISTS userrole")
