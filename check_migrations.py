import asyncio
from app.database import engine
import sqlalchemy as sa

async def check_migrations():
    async with engine.begin() as conn:
        result = await conn.execute(sa.text('SELECT version_num FROM alembic_version ORDER BY version_num'))
        rows = result.fetchall()
        print("Applied migrations:")
        for row in rows:
            print(f"  - {row[0]}")

asyncio.run(check_migrations())
