"""Add defaulter tracking columns and DefaulterFlag table

Revision ID: 20260627_add_defaulter_tracking
Revises: 20260626_username_case_sensitive
Create Date: 2026-06-27

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260627_add_defaulter_tracking"
down_revision = "20260626_username_case_sensitive"
branch_labels = None
depends_on = None


def upgrade():
    # Loan: defaulter tracking + updated_at
    op.add_column("loans", sa.Column("is_defaulter", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("loans", sa.Column("defaulter_flagged_date", sa.DateTime(), nullable=True))
    op.add_column("loans", sa.Column("updated_at", sa.DateTime(), nullable=True))

    # Installment: payment method/reference
    op.add_column("installments", sa.Column("payment_method", sa.String(length=30), nullable=True))
    op.add_column("installments", sa.Column("reference_number", sa.String(length=100), nullable=True))

    # Arrears: updated_at
    op.add_column("arrears", sa.Column("updated_at", sa.DateTime(), nullable=True))

    # New table: defaulter_flags
    op.create_table(
        "defaulter_flags",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("loan_id", sa.Integer(), sa.ForeignKey("loans.id"), nullable=False),
        sa.Column("customer_id", sa.String(length=30), sa.ForeignKey("customers.id_number"), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("days_checked", sa.Integer(), nullable=True),
        sa.Column("required_amount", sa.Float(), nullable=True),
        sa.Column("actual_amount", sa.Float(), nullable=True),
        sa.Column("checked_date", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("defaulter_flags")

    op.drop_column("arrears", "updated_at")

    op.drop_column("installments", "reference_number")
    op.drop_column("installments", "payment_method")

    op.drop_column("loans", "updated_at")
    op.drop_column("loans", "defaulter_flagged_date")
    op.drop_column("loans", "is_defaulter")
