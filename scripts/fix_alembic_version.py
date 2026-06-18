"""Widen alembic_version.version_num so long revision IDs can be stored."""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import engine


async def fix_alembic_version_column() -> None:
    async with engine.begin() as conn:
        current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        print(f"Current alembic version: {current}")

        await conn.execute(
            text("ALTER TABLE alembic_version MODIFY version_num VARCHAR(64) NOT NULL")
        )
        print("Widened alembic_version.version_num to VARCHAR(64)")


if __name__ == "__main__":
    asyncio.run(fix_alembic_version_column())
