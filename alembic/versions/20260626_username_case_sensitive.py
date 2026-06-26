"""make_username_case_sensitive

Revision ID: 20260626_username_case_sensitive
Revises: 20260626_fix_userrole_enum_case
Create Date: 2026-06-26 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "20260626_username_case_sensitive"
down_revision = "20260626_fix_userrole_enum_case"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users MODIFY COLUMN username VARCHAR(50) "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE users MODIFY COLUMN username VARCHAR(50) "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL"
    )
