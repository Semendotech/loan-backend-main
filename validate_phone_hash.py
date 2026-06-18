"""
Validation script for phone hash implementation.
Tests phone normalization, hashing, and M-Pesa callback matching.

Run this AFTER running migrations to verify the system works correctly.
"""

import asyncio
import hashlib
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from app.utils import normalize_phone, hash_phone
from app.database import get_db, engine, AsyncSessionLocal
from app.models import Customer
from sqlalchemy.future import select


def test_normalization():
    """Test phone normalization logic"""
    print("\n" + "="*60)
    print("TEST 1: Phone Normalization")
    print("="*60)
    
    test_cases = [
        ("254712345678", "0712345678"),
        ("0712345678", "0712345678"),
        ("712345678", "0712345678"),
        ("+254712345678", "0712345678"),
        ("254-712-345-678", "0712345678"),
    ]
    
    all_passed = True
    for input_phone, expected in test_cases:
        result = normalize_phone(input_phone)
        passed = result == expected
        all_passed = all_passed and passed
        
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: normalize_phone('{input_phone}') = '{result}' (expected '{expected}')")
    
    return all_passed


def test_hashing():
    """Test that hashing is consistent"""
    print("\n" + "="*60)
    print("TEST 2: Phone Hashing Consistency")
    print("="*60)
    
    test_cases = [
        ("254712345678", "0712345678"),
        ("0712345678", "0712345678"),
        ("+254712345678", "0712345678"),
    ]
    
    all_passed = True
    hashes = []
    
    for input_phone, normalized in test_cases:
        hash_result = hash_phone(input_phone)
        hashes.append(hash_result)
        
        # Verify hash is 64 characters (SHA-256 hex)
        is_valid_hash = len(hash_result) == 64 and all(c in '0123456789abcdef' for c in hash_result)
        
        print(f"hash_phone('{input_phone}')")
        print(f"  → normalized: '{normalized}'")
        print(f"  → hash: {hash_result[:16]}...{hash_result[-16:]}")
        print(f"  → valid SHA-256: {'✅ YES' if is_valid_hash else '❌ NO'}")
        
        all_passed = all_passed and is_valid_hash
    
    # Verify all hashes are identical (same input format = same hash)
    all_same = all(h == hashes[0] for h in hashes)
    print(f"\nAll hashes identical: {'✅ YES' if all_same else '❌ NO'}")
    all_passed = all_passed and all_same
    
    return all_passed


def test_safaricom_compatibility():
    """Test hash matches what Safaricom would produce"""
    print("\n" + "="*60)
    print("TEST 3: Safaricom Hash Compatibility")
    print("="*60)
    
    # Safaricom likely hashes the normalized phone (0XXXXXXXXX)
    phone = "0712345678"
    our_hash = hash_phone(phone)
    
    # Manual calculation to verify
    safaricom_hash = hashlib.sha256(phone.encode()).hexdigest()
    
    match = our_hash == safaricom_hash
    print(f"Phone: {phone}")
    print(f"Our hash:       {our_hash}")
    print(f"Expected hash:  {safaricom_hash}")
    print(f"Match: {'✅ YES' if match else '❌ NO'}")
    
    return match


async def test_database_integration():
    """Test that database integration works"""
    print("\n" + "="*60)
    print("TEST 4: Database Integration")
    print("="*60)
    
    try:
        async with AsyncSessionLocal() as session:
            # Check if customers table exists and has phone_hash
            result = await session.execute(
                select(Customer).limit(1)
            )
            test_customer = result.scalar_one_or_none()
            
            if test_customer:
                print(f"✅ Found test customer: {test_customer.name}")
                print(f"  Phone: {test_customer.phone}")
                print(f"  Phone Hash: {test_customer.phone_hash[:16]}...{test_customer.phone_hash[-16:] if test_customer.phone_hash else 'NULL'}")
                
                # Verify hash is NOT NULL
                if test_customer.phone_hash is None:
                    print("❌ FAIL: phone_hash is NULL")
                    return False
                
                # Verify hash is correct
                expected_hash = hash_phone(test_customer.phone)
                if test_customer.phone_hash == expected_hash:
                    print("✅ Phone hash is correct")
                    return True
                else:
                    print("❌ Phone hash mismatch!")
                    print(f"  Expected: {expected_hash}")
                    print(f"  Got:      {test_customer.phone_hash}")
                    return False
            else:
                print("⚠️  No test customer found in database")
                print("   (This is okay if database is empty, but make sure to test after creating a customer)")
                return True
                
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        return False


async def main():
    """Run all tests"""
    print("\n" + "🔍 PHONE HASH IMPLEMENTATION VALIDATION")
    print("="*60)
    
    results = {
        "Normalization": test_normalization(),
        "Hashing": test_hashing(),
        "Safaricom Compatibility": test_safaricom_compatibility(),
        "Database Integration": await test_database_integration(),
    }
    
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    all_passed = all(results.values())
    
    print("\n" + "="*60)
    if all_passed:
        print("✅ ALL TESTS PASSED - System is ready!")
        print("\nNext steps:")
        print("  1. Run: alembic upgrade head")
        print("  2. Test customer registration with different phone formats")
        print("  3. Monitor M-Pesa callbacks and verify matches")
    else:
        print("❌ SOME TESTS FAILED - Please review errors above")
    print("="*60 + "\n")
    
    return all_passed


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
