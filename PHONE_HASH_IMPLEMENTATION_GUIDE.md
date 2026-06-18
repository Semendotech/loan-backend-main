# Phone Hash Implementation - Complete Guide

## 🎯 What Was Fixed

This implementation fixes the M-Pesa payment callback system to reliably match payments to customer profiles using phone number hashing instead of customer names. The system now handles phone number format inconsistencies automatically.

### Problem Solved
- ❌ **Before**: Phone number inconsistencies (254... vs 0...) caused hash mismatches → payments didn't match customers
- ✅ **After**: All phones normalized to consistent format → reliable payment matching

---

## 📋 Files Changed

### 1. **app/utils.py** ✅
**Changes**:
- Added `normalize_phone(phone: str)` function
  - Converts "254712345678" → "0712345678"
  - Converts "+254712345678" → "0712345678"
  - Converts "712345678" → "0712345678"
  - Always returns "0XXXXXXXXX" format

- Updated `hash_phone()` to normalize BEFORE hashing
  - Now calls `normalize_phone()` internally
  - Ensures same phone = same hash every time

**Why**: Consistency guarantee - Safaricom hashes the normalized phone, so we must hash the same format.

---

### 2. **app/models.py** ✅
**Changes**:
```python
# BEFORE: nullable=True
phone_hash = Column(String(64), unique=True, nullable=True, index=True)

# AFTER: nullable=False
phone_hash = Column(String(64), unique=True, nullable=False, index=True)
```

**Why**: Prevents NULL values in production. Every customer MUST have a hash.

---

### 3. **app/routes/customer_routes.py** ✅
**Changes**:
- Added import: `from ..utils import hash_phone, normalize_phone`
- Updated `create_customer()` endpoint:
  ```python
  # Normalize phone BEFORE uniqueness check and hashing
  normalized_phone = normalize_phone(customer.phone)
  
  # Use normalized phone for uniqueness check
  existing = await db.execute(
      select(Customer).filter(Customer.phone == normalized_phone)
  )
  
  # Store normalized phone and its hash
  payload["phone"] = normalized_phone
  payload["phone_hash"] = hash_phone(normalized_phone)
  ```

**Why**: Ensures database consistency. Same phone format for all customers, unique constraints work properly.

---

### 4. **app/routes/mpesa_routes.py** ✅
**Changes**:
- Improved validation: `if not all([trans_id, msisdn_hash, amount > 0])`
- Enhanced logging for debugging
- Direct hash matching (no changes needed - Safaricom sends pre-hashed MSISDN)

**Why**: Better error detection and debugging capabilities.

---

### 5. **alembic/versions/20260618_add_phone_hash.py** ✅
**Changes**:
- Added `normalize_phone()` helper function
- Updated backfill logic to normalize phones BEFORE hashing:
  ```python
  normalized = normalize_phone(phone)
  phone_hash = hashlib.sha256(normalized.encode()).hexdigest()
  connection.execute(
      sa.text("UPDATE customers SET phone = :phone, phone_hash = :hash WHERE id = :id"),
      {"phone": normalized, "hash": phone_hash, "id": customer_id}
  )
  ```

**Why**: Fixes any existing inconsistent phone formats during initial migration.

---

### 6. **alembic/versions/20260618_make_phone_hash_not_null.py** ✅ (NEW)
**Purpose**: Second migration to enforce `phone_hash NOT NULL`
- Backfills any remaining NULL hashes
- Normalizes all phones to consistent format
- Makes column NOT NULL

**Why**: Two-step process ensures data integrity. First migration adds column & populates, second enforces constraint.

---

### 7. **apply_phone_hash.py** ✅
**Changes**:
- Added phone normalization to the manual backfill script
- Handles NULL phone_hash values
- Normalizes existing phones during backfill

**Why**: Alternative way to apply phone hashes if direct migration fails.

---

### 8. **validate_phone_hash.py** ✅ (NEW)
**Purpose**: Validation/testing script
- Tests phone normalization logic
- Tests hashing consistency
- Verifies Safaricom compatibility
- Tests database integration

**Run it**: `python validate_phone_hash.py`

---

## 🚀 Deployment Steps

### Step 1: Deploy Code Changes
```bash
# Pull the updated code
git pull

# The following files are modified:
# - app/utils.py
# - app/models.py
# - app/routes/customer_routes.py
# - app/routes/mpesa_routes.py
# - apply_phone_hash.py
# - alembic/versions/20260618_add_phone_hash.py (updated)

# The following files are NEW:
# - alembic/versions/20260618_make_phone_hash_not_null.py
# - validate_phone_hash.py
```

### Step 2: Run Database Migrations
```bash
# Run all pending migrations
alembic upgrade head

# This will:
# 1. Add phone_hash column (if not exists)
# 2. Normalize all existing phones
# 3. Compute and store hashes for all customers
# 4. Make phone_hash NOT NULL
```

### Step 3: Validate System
```bash
# Run validation script
python validate_phone_hash.py

# Expected output:
# ✅ PASS: Normalization
# ✅ PASS: Hashing
# ✅ PASS: Safaricom Compatibility
# ✅ PASS: Database Integration
```

### Step 4: Test in Sandbox
1. **Register a test customer** with different phone formats:
   - "254712345678"
   - "0712345678"
   - "+254712345678"
   - All should normalize to "0712345678" ✅

2. **Verify database**:
   ```sql
   SELECT id, name, phone, phone_hash FROM customers WHERE name = 'Test Customer';
   -- Should show normalized phone and hash
   ```

3. **Test M-Pesa callback**:
   - Send test payment via M-Pesa sandbox
   - Check logs for: "Phone hash lookup: Found customer [Name]"
   - Verify payment recorded successfully ✅

4. **Check SMS**:
   - Customer should receive SMS with correct balance
   - Confirms phone number is correct (not hashed) ✅

---

## 📊 How the System Works Now

### Customer Registration
```
Frontend Input: "254712345678" (any format)
                        ↓
         normalize_phone() → "0712345678"
                        ↓
    Store phone: "0712345678" (normalized)
    Store phone_hash: "d6f1734a..." (SHA-256)
                        ↓
         Database updated ✅
```

### M-Pesa Payment Arrives
```
Safaricom sends:
{
  "TransID": "ABC123XYZ",
  "TransAmount": 5000,
  "MSISDN": "d6f1734a...",  (pre-hashed by Safaricom)
  "BillRefNumber": "..."
}
                        ↓
    Query: SELECT * FROM customers 
           WHERE phone_hash = "d6f1734a..."
                        ↓
         MATCH! Found customer Alice ✅
                        ↓
    Create installment for Alice's loan
    Send SMS to "0712345678" (plain phone)
    Update balance ✅
```

---

## 🔍 Monitoring & Debugging

### Check for Unmatched Payments
```sql
-- M-Pesa callbacks that couldn't find a customer
SELECT trans_id, amount, phone, created_at 
FROM mpesa_transactions 
WHERE loan_id IS NULL
ORDER BY created_at DESC
LIMIT 10;
```

### Verify Phone Consistency
```sql
-- Check for any non-normalized phones (should be 10 chars with leading 0)
SELECT id, phone, phone_hash 
FROM customers 
WHERE phone NOT LIKE '0%' OR LENGTH(phone) != 10;
-- Should return: No results ✅
```

### Test Hash Computation
```sql
-- Verify a customer's hash
SELECT id, name, phone, phone_hash 
FROM customers 
WHERE id = 1;

-- In Python:
from app.utils import hash_phone
expected = hash_phone("0712345678")
-- Should match phone_hash from database ✅
```

---

## ⚠️ Important Notes

### Before Migration
- Back up your database
- Test migrations on staging/development first
- Have rollback plan ready (though downgrade is supported)

### After Migration
- All phones will be normalized automatically
- All hashes computed automatically
- No manual data manipulation needed

### Phone Format Consistency
- Frontend should accept multiple formats
- Backend normalizes automatically
- Database stores: "0XXXXXXXXX"
- M-Pesa callbacks: hashed format

### Security Implications
- Phone hashes are SHA-256 (one-way, cryptographically secure)
- Even with database access, hashes can't be reversed to get phone
- Plain phone is encrypted at rest (depending on infrastructure)
- SMS is sent to plain phone number (never hashed)

---

## 🧪 Test Scenarios

### Scenario 1: New Customer Registration
```
Input: Phone "254712345678"
Expected: Stored as "0712345678", hash computed correctly
Test: Register customer, query DB, verify both fields
```

### Scenario 2: M-Pesa Payment Matching
```
Setup: Customer with phone "0712345678"
Event: M-Pesa payment arrives with MSISDN hash
Expected: Customer matched and payment recorded
Test: Send sandbox payment, check logs for "Found customer"
```

### Scenario 3: Duplicate Phone Formats
```
Attempt: Register two customers with "254712345678" and "0712345678"
Expected: Second registration fails (duplicate)
Test: Verify uniqueness constraint works
```

### Scenario 4: SMS Notification
```
Setup: Customer with normalized phone
Event: Payment received
Expected: SMS sent to "0712345678"
Test: Check SMS delivery logs
```

---

## 📞 Troubleshooting

### Problem: Payment not matching customer
**Check**:
1. Customer phone is normalized (SELECT * FROM customers WHERE id = X)
2. Customer has phone_hash (should not be NULL)
3. M-Pesa callback logs show hash lookup result
4. Compare actual hash with computed hash

```python
from app.utils import hash_phone
hash_phone("0712345678")  # Should match DB phone_hash
```

### Problem: SMS not sending
**Check**:
1. Customer phone is plain format (0XXXXXXXXX), not hashed
2. Africa's Talking API key configured
3. Phone conversion to international format: `+254712345678`
4. Check SMS API response in logs

### Problem: Migration failed
**Steps**:
1. Check migration status: `alembic current`
2. Review migration logs for errors
3. If stuck: `alembic stamp <version>` to skip (with caution)
4. Run: `alembic upgrade head`

---

## ✅ Rollback Plan (if needed)

```bash
# Revert to previous migration
alembic downgrade <previous_version>

# This will:
# - Drop phone_hash NOT NULL constraint
# - Keep phone hashes (data preserved)
# - Revert code changes

# To fully rollback:
# 1. Revert code commits
# 2. Drop phone_hash column manually if needed
# 3. Restore from backup if critical data lost
```

---

## 📈 Monitoring Metrics

Track these after deployment:

1. **Payment Matching Rate**
   ```
   Matched payments / Total M-Pesa callbacks = Should be ~98%+
   ```

2. **Hash Mismatch Rate**
   - Should be <2% (only for non-existent customers)
   - If >5%, investigate normalization logic

3. **SMS Delivery Rate**
   - Should be ~95%+ (limited by SMS provider)
   - Check both logs and SMS API responses

4. **Database Integrity**
   ```sql
   -- All phones normalized
   SELECT COUNT(*) WHERE phone NOT LIKE '0%' OR LENGTH(phone) != 10;
   -- Result: 0
   
   -- No NULL hashes
   SELECT COUNT(*) WHERE phone_hash IS NULL;
   -- Result: 0
   ```

---

## 🎉 Success Criteria

✅ **System is working correctly when**:
- All customers have normalized phones (0XXXXXXXXX)
- All customers have non-NULL phone_hash
- M-Pesa payments match customers by hash
- SMS is sent to correct phone number
- No unmatched payment callbacks (unless customer doesn't exist)

---

## Questions?

Check logs in this order:
1. App logs: Look for "Phone hash lookup" messages
2. Database: Query `customers` table directly
3. M-Pesa dashboard: Verify callbacks were sent
4. SMS provider: Verify delivery status

Run validation script anytime: `python validate_phone_hash.py`
