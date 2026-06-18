import asyncio
from app.database import engine
import sqlalchemy as sa
import hashlib

def normalize_phone(phone: str) -> str:
    """
    Normalize a phone number to a consistent format.
    Converts international format (254...) to local format (0...)
    """
    phone = ''.join(filter(str.isdigit, phone))
    if phone.startswith('254'):
        phone = '0' + phone[3:]
    elif not phone.startswith('0') and len(phone) == 9:
        phone = '0' + phone
    return phone

async def apply_phone_hash_manually():
    """
    Since tables already exist, we'll manually add the phone_hash column and backfill if needed.
    Normalizes all phone numbers and computes hashes consistently.
    """
    async with engine.begin() as conn:
        # Check if phone_hash column exists
        result = await conn.execute(sa.text("""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME='customers' AND COLUMN_NAME='phone_hash'
        """))
        
        phone_hash_exists = result.scalar() is not None
        
        if not phone_hash_exists:
            print("Adding phone_hash column...")
            await conn.execute(sa.text("""
                ALTER TABLE customers ADD COLUMN phone_hash VARCHAR(64) UNIQUE
            """))
            
            # Create the index
            await conn.execute(sa.text("""
                CREATE INDEX ix_customers_phone_hash ON customers(phone_hash)
            """))
            
            print("Backfilling phone_hash for existing customers...")
            # Get all customers and hash their phones
            result = await conn.execute(sa.text("SELECT id, phone FROM customers WHERE phone IS NOT NULL"))
            rows = result.fetchall()
            
            for row in rows:
                customer_id, phone = row
                # IMPORTANT: Normalize phone before hashing
                normalized = normalize_phone(phone)
                phone_hash = hashlib.sha256(normalized.encode()).hexdigest()
                
                # Update both phone (normalized) and phone_hash
                await conn.execute(
                    sa.text("UPDATE customers SET phone = :phone, phone_hash = :hash WHERE id = :id"),
                    {"phone": normalized, "hash": phone_hash, "id": customer_id}
                )
            
            print(f"Backfilled {len(rows)} customer records with normalized phones and hashes")
        else:
            print("phone_hash column already exists")
            
            # Check for any NULL phone_hash values and backfill them
            result = await conn.execute(sa.text("SELECT id, phone FROM customers WHERE phone_hash IS NULL AND phone IS NOT NULL"))
            rows = result.fetchall()
            
            if rows:
                print(f"Backfilling {len(rows)} customers with NULL phone_hash...")
                for row in rows:
                    customer_id, phone = row
                    normalized = normalize_phone(phone)
                    phone_hash = hashlib.sha256(normalized.encode()).hexdigest()
                    await conn.execute(
                        sa.text("UPDATE customers SET phone = :phone, phone_hash = :hash WHERE id = :id"),
                        {"phone": normalized, "hash": phone_hash, "id": customer_id}
                    )
        
        # Mark migration as applied
        await conn.execute(sa.text("""
            INSERT IGNORE INTO alembic_version (version_num) VALUES ('20260618_add_phone_hash')
        """))
        print("Phone hash migration marked as complete")

if __name__ == '__main__':
    asyncio.run(apply_phone_hash_manually())
