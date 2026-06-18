"""Renormalize customer phones to 254 format and refresh phone_hash values.

Revision ID: 20260619_renormalize_phone_hash_254
Revises: 20260618_make_phone_hash_not_null
Create Date: 2026-06-19 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
import hashlib
import re

revision = "20260619_renormalize_phone_hash_254"
down_revision = "20260618_make_phone_hash_not_null"
branch_labels = None
depends_on = None

PHONE_PATTERN = re.compile(r"^254\d{9,10}$")


def normalize_phone(phone: str) -> str:
    """Mirror app.utils.phone.normalize_phone for migration backfill."""
    if not phone:
        raise ValueError(f"Invalid phone format: {phone}")

    cleaned = re.sub(r"[\s\-\(\)]", "", phone.strip())

    if cleaned.startswith("+"):
        cleaned = cleaned[1:]

    if cleaned.startswith("00254"):
        cleaned = "254" + cleaned[5:]

    if cleaned.startswith("0") and len(cleaned) == 10:
        cleaned = "254" + cleaned[1:]

    if not PHONE_PATTERN.match(cleaned):
        raise ValueError(f"Invalid phone format: {phone}")

    return cleaned


def hash_phone(phone: str) -> str:
    """Mirror app.utils.phone.hash_phone for migration backfill."""
    return hashlib.sha256(phone.encode()).hexdigest()


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {column["name"] for column in inspector.get_columns("customers")}

    if "phone_hash" not in columns:
        op.add_column(
            "customers",
            sa.Column("phone_hash", sa.String(length=64), nullable=True),
        )
        op.create_index(
            op.f("ix_customers_phone_hash"),
            "customers",
            ["phone_hash"],
            unique=True,
        )

    result = connection.execute(
        sa.text("SELECT id, phone FROM customers WHERE phone IS NOT NULL")
    )

    for customer_id, phone in result:
        try:
            normalized = normalize_phone(phone)
            phone_hash = hash_phone(normalized)
        except ValueError as exc:
            print(f"Skipping customer {customer_id} ({phone}): {exc}")
            continue

        connection.execute(
            sa.text(
                "UPDATE customers SET phone = :phone, phone_hash = :phone_hash WHERE id = :id"
            ),
            {"phone": normalized, "phone_hash": phone_hash, "id": customer_id},
        )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {column["name"] for column in inspector.get_columns("customers")}

    if "phone_hash" not in columns:
        return

    result = connection.execute(
        sa.text("SELECT id, phone FROM customers WHERE phone IS NOT NULL")
    )

    for customer_id, phone in result:
        digits = "".join(filter(str.isdigit, phone))
        if digits.startswith("254") and len(digits) >= 12:
            local_phone = "0" + digits[3:]
        else:
            local_phone = digits

        connection.execute(
            sa.text("UPDATE customers SET phone = :phone WHERE id = :id"),
            {"phone": local_phone, "id": customer_id},
        )

    op.drop_index(op.f("ix_customers_phone_hash"), table_name="customers")
    op.drop_column("customers", "phone_hash")
