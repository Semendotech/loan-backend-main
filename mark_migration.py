import asyncio
from app.database import engine
import sqlalchemy as sa

async def mark_migrations():
    async with engine.begin() as conn:
        # Insert migration records if not present
        await conn.execute(sa.text("""
            INSERT IGNORE INTO alembic_version (version_num) VALUES 
            ('835018742018'),
            ('20260615_add_mpesa_transactions')
        """))
        print('Migrations marked as complete')

if __name__ == '__main__':
    asyncio.run(mark_migrations())

