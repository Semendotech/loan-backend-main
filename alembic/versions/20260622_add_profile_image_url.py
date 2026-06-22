"""add profile_image_url to customers

Revision ID: 20260622_add_profile_image_url
Revises: 20260620_add_user_role_and_payment_metadata
Create Date: 2026-06-22 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "20260622_add_profile_image_url"
down_revision = "20260620_add_user_role_and_payment_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {column["name"] for column in inspector.get_columns("customers")}

    if "profile_image_url" not in columns:
        op.add_column(
            "customers",
            sa.Column("profile_image_url", sa.String(length=512), nullable=True),
        )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {column["name"] for column in inspector.get_columns("customers")}

    if "profile_image_url" in columns:
        op.drop_column("customers", "profile_image_url")
