"""fix_userrole_enum_case

Revision ID: 20260626_fix_userrole_enum_case
Revises: 20260626_add_missing_user_columns
Create Date: 2026-06-26 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "20260626_fix_userrole_enum_case"
down_revision = "20260626_add_missing_user_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: widen to VARCHAR so old and new casing can coexist during migration
    op.execute("ALTER TABLE users MODIFY COLUMN role VARCHAR(20) NOT NULL DEFAULT 'USER'")
    # Step 2: migrate data to new casing
    op.execute("UPDATE users SET role = 'ADMIN' WHERE role = 'admin'")
    op.execute("UPDATE users SET role = 'USER' WHERE role = 'loan_officer'")
    # Step 3: narrow to the enum models.py expects
    op.execute("ALTER TABLE users MODIFY COLUMN role ENUM('ADMIN','USER') NOT NULL DEFAULT 'USER'")


def downgrade() -> None:
    op.execute("ALTER TABLE users MODIFY COLUMN role VARCHAR(20) NOT NULL DEFAULT 'USER'")
    op.execute("UPDATE users SET role = 'admin' WHERE role = 'ADMIN'")
    op.execute("UPDATE users SET role = 'loan_officer' WHERE role = 'USER'")
    op.execute("ALTER TABLE users MODIFY COLUMN role ENUM('admin','loan_officer') NOT NULL DEFAULT 'loan_officer'")
