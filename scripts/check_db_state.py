import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import engine


async def check() -> None:
    async with engine.connect() as conn:
        version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        print("alembic version:", version)

        version_col = (
            await conn.execute(text("SHOW COLUMNS FROM alembic_version LIKE 'version_num'"))
        ).fetchone()
        print("version_num column:", version_col)

        phone_hash_col = (
            await conn.execute(text("SHOW COLUMNS FROM customers LIKE 'phone_hash'"))
        ).fetchone()
        print("phone_hash column:", phone_hash_col)

        rows = (await conn.execute(text("SELECT id, phone, phone_hash FROM customers LIMIT 5"))).fetchall()
        for row in rows:
            print("customer:", row)


if __name__ == "__main__":
    asyncio.run(check())
