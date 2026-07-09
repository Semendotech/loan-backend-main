lines = open('app/services/loan_service.py', encoding='utf-8').readlines()

# Find start and end of check_defaulter_status
start = next(i for i, l in enumerate(lines) if 'def check_defaulter_status' in l)
end = next(i for i in range(start+1, len(lines)) if 'def record_payment' in lines[i])

new_func = """    def check_defaulter_status(db: Session, loan_id: int) -> bool:
        \"\"\"
        Check if loan should be flagged as DEFAULTER.

        Rule: DEFAULTER if in ACTIVE period (days 1-30) AND
              no payment made in the last 5 consecutive days.

        Returns True if flagged, False otherwise.
        \"\"\"
        loan = db.query(Loan).filter(Loan.id == loan_id).first()
        if not loan:
            return False

        # Only check during ACTIVE period
        if not loan.is_active_period:
            return False

        # Find the most recent payment date
        last_payment = db.query(func.max(Installment.payment_date)).filter(
            Installment.loan_id == loan_id,
        ).scalar()

        today = now_eat()

        if last_payment is None:
            # No payments at all - defaulter if loan is 5+ days old
            days_since_start = loan.days_since_start
            is_defaulter = days_since_start >= 5
        else:
            # Make last_payment timezone-aware if needed
            if last_payment.tzinfo is None:
                from zoneinfo import ZoneInfo
                last_payment = last_payment.replace(tzinfo=ZoneInfo("Africa/Nairobi"))
            days_since_last_payment = (today - last_payment).days
            is_defaulter = days_since_last_payment >= 5

        # Update loan if status changed
        if is_defaulter and not loan.is_defaulter:
            loan.is_defaulter = True
            loan.defaulter_flagged_date = today

            flag_record = DefaulterFlag(
                loan_id=loan_id,
                customer_id=loan.customer_id,
                action="FLAGGED",
                reason=f"No payment received in 5 or more consecutive days.",
                days_checked=5,
                required_amount=None,
                actual_amount=None,
            )
            db.add(flag_record)
            db.commit()
            return True

        elif not is_defaulter and loan.is_defaulter:
            loan.is_defaulter = False

            flag_record = DefaulterFlag(
                loan_id=loan_id,
                customer_id=loan.customer_id,
                action="CLEARED",
                reason=f"Payment received within last 5 days.",
                days_checked=5,
                required_amount=None,
                actual_amount=None,
            )
            db.add(flag_record)
            db.commit()
            return False

        db.commit()
        return is_defaulter

"""

final = lines[:start] + [new_func] + lines[end:]
open('app/services/loan_service.py', 'w', encoding='utf-8').writelines(final)
print(f"Done. Replaced lines {start+1} to {end}.")
