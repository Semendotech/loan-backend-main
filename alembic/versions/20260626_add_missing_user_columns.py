"""add_missing_user_columns

Revision ID: 20260626_add_missing_user_columns
Revises: 20260622_add_profile_image_url
Create Date: 2026-06-26 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260626_add_missing_user_columns"
down_revision = "20260622_add_profile_image_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('email', sa.String(length=120), nullable=True))
    op.add_column('users', sa.Column('last_name', sa.String(length=50), nullable=True))
    op.add_column('users', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('users', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.create_unique_constraint('uq_users_email', 'users', ['email'])


def downgrade() -> None:
    op.drop_constraint('uq_users_email', 'users', type_='unique')
    op.drop_column('users', 'updated_at')
    op.drop_column('users', 'is_active')
    op.drop_column('users', 'last_name')
    op.drop_column('users', 'email')
