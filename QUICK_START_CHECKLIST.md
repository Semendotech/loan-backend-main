# Quick Start - Phone Hash Deployment Checklist

## ⏱️ Estimated Time: 30 minutes

---

## ✅ PRE-DEPLOYMENT (5 min)

- [ ] Read: `IMPLEMENTATION_SUMMARY.md`
- [ ] Read: `PHONE_HASH_IMPLEMENTATION_GUIDE.md`
- [ ] Backup database: `cp your_db.db your_db.db.backup-$(date +%Y%m%d)`
- [ ] Verify no active M-Pesa transactions pending

---

## 🚀 DEPLOYMENT (10 min)

```bash
# 1. Pull code changes
git pull

# 2. Run migrations
cd loan-backend-main
alembic upgrade head

# Expected output:
# INFO [alembic.runtime.migration] Running upgrade 20260615_add_mpesa_transactions -> 20260618_add_phone_hash
# INFO [alembic.runtime.migration] Running upgrade 20260618_add_phone_hash -> 20260618_make_phone_hash_not_null
# Done!
```

---

## 🧪 VALIDATION (10 min)

```bash
# 1. Run validation script
python validate_phone_hash.py

# Expected output:
# ✅ PASS: Normalization
# ✅ PASS: Hashing
# ✅ PASS: Safaricom Compatibility
# ✅ PASS: Database Integration
# ✅ ALL TESTS PASSED - System is ready!

# 2. Verify database
python manage.py shell  # or: sqlite3 your_db.db

>>> from app.models import Customer
>>> from sqlalchemy import select
>>> # Check a customer
>>> c = db.query(Customer).first()
>>> print(f"Phone: {c.phone}")       # Should be: 0712345678
>>> print(f"Hash: {c.phone_hash}")   # Should be: 64 char string (NOT NULL)
```

---

## 📊 POST-DEPLOYMENT (5 min)

### Immediate Checks
- [ ] No error logs related to phone_hash
- [ ] No migration errors in database
- [ ] validate_phone_hash.py passes all tests

### Within 1 Hour
- [ ] Test customer registration with different phone formats
- [ ] Verify all customers have normalized phones
- [ ] Verify all customers have non-NULL phone_hash

### Within 24 Hours
- [ ] Monitor M-Pesa callbacks in logs
- [ ] Check for "Phone hash lookup: Found" messages
- [ ] Verify SMS sending works correctly
- [ ] Check for any unmatched payment callbacks

---

## 🔧 TROUBLESHOOTING

### Migration Failed?
```bash
# 1. Check current migration status
alembic current

# 2. Review migration file for errors
cat alembic/versions/20260618_make_phone_hash_not_null.py

# 3. Check logs for specific error
# 4. Roll back if needed (ONLY if there's an issue):
# alembic downgrade <previous_version>
```

### Validation Script Failed?
```bash
# 1. Check Python environment
python --version

# 2. Check imports
python -c "from app.utils import normalize_phone; print(normalize_phone('254712345678'))"

# 3. Check database connection
python -c "from app.database import engine; print('Connected!' if engine else 'Failed')"
```

### Payment Not Matching?
```bash
# 1. Check customer phone format
sqlite3 your_db.db "SELECT phone, phone_hash FROM customers WHERE id = X;"

# 2. Verify hash computation
python << 'EOF'
from app.utils import hash_phone
phone = "0712345678"
print(hash_phone(phone))
EOF

# 3. Check M-Pesa callback logs for MSISDN value
grep "MSISDN" your_app_logs.log | tail -5
```

---

## 📞 EMERGENCY ROLLBACK

**Only if critical issue found** (you have 5-minute backup):

```bash
# 1. Restore database backup
cp your_db.db.backup-20260618 your_db.db

# 2. Rollback code
git revert <commit_hash>

# 3. Downgrade migrations
alembic downgrade <previous_migration>

# 4. Restart application
```

---

## ✨ SUCCESS INDICATORS

You'll know everything worked when:

✅ **Database**
```
SELECT COUNT(*), COUNT(DISTINCT phone_hash) FROM customers;
-- Both numbers should be equal (no NULLs)

SELECT COUNT(*) FROM customers WHERE phone NOT LIKE '0%';
-- Result: 0 (all normalized)
```

✅ **Logs**
```
grep "Phone hash lookup: Found" app_logs.log
-- Should see matches for M-Pesa payments
```

✅ **SMS**
```
Customer receives: "Payment received! KSh 5000 paid... Balance: KSh 45000"
-- No issues with phone number
```

✅ **Validation Script**
```
python validate_phone_hash.py
-- All 4 tests PASS
```

---

## 📈 MONITORING (Ongoing)

Add these checks to your monitoring:

```bash
# Weekly: Database integrity
python << 'EOF'
from app.database import AsyncSessionLocal
from app.models import Customer
from sqlalchemy import func, select

async def check():
    async with AsyncSessionLocal() as db:
        # Check for NULL phone_hash
        result = await db.execute(
            select(func.count()).select_from(Customer).where(Customer.phone_hash == None)
        )
        null_count = result.scalar()
        print(f"Customers with NULL phone_hash: {null_count}")
        assert null_count == 0, "Found NULL phone_hash!"
        
        # Check for non-normalized phones
        result = await db.execute(
            select(func.count()).select_from(Customer).where(
                (Customer.phone.notlike('0%')) | (func.length(Customer.phone) != 10)
            )
        )
        bad_count = result.scalar()
        print(f"Non-normalized phones: {bad_count}")
        assert bad_count == 0, "Found non-normalized phones!"

import asyncio
asyncio.run(check())
EOF

# Daily: M-Pesa callback success rate
grep "Phone hash lookup" app_logs.log | wc -l  # Total lookups
grep "Phone hash lookup: Found" app_logs.log | wc -l  # Successful matches
```

---

## 📚 DOCUMENTATION FILES

| File | Purpose | Read When |
|------|---------|-----------|
| `IMPLEMENTATION_SUMMARY.md` | High-level overview | **Start here** |
| `PHONE_HASH_IMPLEMENTATION_GUIDE.md` | Complete technical guide | **Before deploying** |
| `validate_phone_hash.py` | Test/validation script | **After deploying** |
| `QUICK_START_CHECKLIST.md` | This file | **During deployment** |

---

## 🎯 DEPLOYMENT SIGN-OFF

- [ ] Pre-deployment checks complete
- [ ] Backup verified
- [ ] Migrations successful
- [ ] Validation tests pass
- [ ] Post-deployment checks pass
- [ ] Monitoring configured
- [ ] Team notified

**Deployment Date**: _______________  
**Deployed By**: _______________  
**Verified By**: _______________  

---

**Questions?** Check `PHONE_HASH_IMPLEMENTATION_GUIDE.md` Section: "Troubleshooting"
