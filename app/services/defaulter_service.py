"""
CORRECTED Defaulter Service
- Implement 5-day consecutive payment rule
- Track defaulter status changes
- Provide defaulter reports
"""

from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import Loan, Installment, LoanStatus, DefaulterFlag


class DefaulterService:
    """Service for defaulter detection and management"""

    @staticmethod
    def check_loan_is_defaulter(db: Session, loan_id: int) -> bool:
        """
        Check if a loan should be flagged as DEFAULTER.
        
        Rule: DEFAULTER if in ACTIVE period (days 1-30) AND
              sum of payments in last 5 consecutive days < (daily_instalment * 5)
        
        Example:
        - Daily instalment: 120
        - Required for 5 days: 600
        - If paid < 600 in last 5 days: DEFAULTER
        
        Returns True if should be flagged, False otherwise.
        """
        loan = db.query(Loan).filter(Loan.id == loan_id).first()
        if not loan:
            return False

        # Only check during ACTIVE period (days 1-30)
        if not loan.is_active_period:
            return False

        daily_instalment = loan.daily_instalment
        required_5_day_amount = daily_instalment * 5

        # Get payments from last 5 days
        five_days_ago = datetime.utcnow() - timedelta(days=5)

        total_paid_5_days = db.query(func.sum(Installment.amount)).filter(
            Installment.loan_id == loan_id,
            Installment.payment_date >= five_days_ago,
        ).scalar() or 0

        # Defaulter if total < required
        return total_paid_5_days < required_5_day_amount

    @staticmethod
    def get_5_day_payment_window(db: Session, loan_id: int) -> dict:
        """
        Get detailed 5-day payment window analysis.
        
        Returns:
        {
            "loan_id": int,
            "daily_instalment": float,
            "required_5_days": float,
            "actual_paid_5_days": float,
            "is_defaulter": bool,
            "days_breakdown": [
                {"date": "2026-06-25", "amount": 120},
                ...
            ]
        }
        """
        loan = db.query(Loan).filter(Loan.id == loan_id).first()
        if not loan:
            return {}

        daily_instalment = loan.daily_instalment
        required_5_day = daily_instalment * 5
        
        # Get last 5 days of payments
        five_days_ago = datetime.utcnow() - timedelta(days=5)
        
        payments = db.query(Installment).filter(
            Installment.loan_id == loan_id,
            Installment.payment_date >= five_days_ago,
        ).order_by(Installment.payment_date).all()

        # Group by date
        by_date = {}
        for payment in payments:
            date_key = payment.payment_date.date()
            if date_key not in by_date:
                by_date[date_key] = 0
            by_date[date_key] += payment.amount

        total_paid = sum(by_date.values())

        # Build day breakdown
        days_breakdown = []
        for i in range(5):
            check_date = (datetime.utcnow() - timedelta(days=4-i)).date()
            amount = by_date.get(check_date, 0)
            days_breakdown.append({
                "date": str(check_date),
                "amount": amount,
            })

        return {
            "loan_id": loan_id,
            "daily_instalment": daily_instalment,
            "required_5_days": required_5_day,
            "actual_paid_5_days": total_paid,
            "is_defaulter": total_paid < required_5_day,
            "shortfall": max(0, required_5_day - total_paid),
            "days_breakdown": days_breakdown,
        }

    @staticmethod
    def get_defaulters(
        db: Session,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list, int]:
        """
        Get all defaulter loans.
        
        Defaulter = ACTIVE loan with is_defaulter == true
        
        Returns: (loans list, total count)
        """
        query = db.query(Loan).filter(
            Loan.is_defaulter == True,
            Loan.status == LoanStatus.ACTIVE,
        )

        total = query.count()
        loans = query.order_by(Loan.defaulter_flagged_date.desc()).limit(limit).offset(offset).all()

        return loans, total

    @staticmethod
    def get_defaulter_details(db: Session, loan_id: int) -> dict:
        """
        Get detailed information about a defaulter loan.
        
        Returns analysis of:
        - Loan details
        - 5-day payment window
        - Payment history
        - When flagged
        """
        loan = db.query(Loan).filter(Loan.id == loan_id).first()
        if not loan:
            return {}

        if not loan.is_defaulter:
            return {"message": "Loan is not flagged as defaulter"}

        # 5-day window analysis
        window = DefaulterService.get_5_day_payment_window(db, loan_id)

        # Recent payments (last 10 days)
        ten_days_ago = datetime.utcnow() - timedelta(days=10)
        recent_payments = db.query(Installment).filter(
            Installment.loan_id == loan_id,
            Installment.payment_date >= ten_days_ago,
        ).order_by(Installment.payment_date.desc()).all()

        # Flag history
        flag_history = db.query(DefaulterFlag).filter(
            DefaulterFlag.loan_id == loan_id,
        ).order_by(DefaulterFlag.checked_date.desc()).limit(5).all()

        return {
            "loan_id": loan_id,
            "customer_id": loan.customer_id,
            "amount": loan.amount,
            "total_amount": loan.total_amount,
            "remaining_amount": loan.remaining_amount,
            "status": loan.status.value,
            "days_since_start": loan.days_since_start,
            "is_defaulter": loan.is_defaulter,
            "defaulter_flagged_date": loan.defaulter_flagged_date,
            "5_day_analysis": window,
            "recent_payments": [
                {
                    "id": p.id,
                    "amount": p.amount,
                    "date": p.payment_date,
                    "method": p.payment_method,
                }
                for p in recent_payments
            ],
            "flag_history": [
                {
                    "action": f.action,
                    "reason": f.reason,
                    "required_amount": f.required_amount,
                    "actual_amount": f.actual_amount,
                    "checked_date": f.checked_date,
                }
                for f in flag_history
            ],
        }

    @staticmethod
    def clear_defaulter_flag(db: Session, loan_id: int) -> bool:
        """
        Manually clear defaulter flag (admin only, if loan catches up).
        
        Use case: Loan was flagged but customer made catches up on payments.
        """
        loan = db.query(Loan).filter(Loan.id == loan_id).first()
        if not loan:
            return False

        if not loan.is_defaulter:
            return False

        # Only clear if customer has caught up (paid enough in last 5 days)
        is_still_defaulter = DefaulterService.check_loan_is_defaulter(db, loan_id)
        
        if is_still_defaulter:
            return False

        loan.is_defaulter = False
        db.commit()

        return True

    @staticmethod
    def get_defaulter_statistics(db: Session) -> dict:
        """
        Get statistics about defaulters.
        
        Returns:
        - Total defaulters
        - Total outstanding on defaulted loans
        - Average outstanding per defaulter
        - Defaulters by days overdue
        """
        defaulters = db.query(Loan).filter(
            Loan.is_defaulter == True,
            Loan.status == LoanStatus.ACTIVE,
        ).all()

        total = len(defaulters)
        total_outstanding = sum(d.remaining_amount for d in defaulters)
        avg_outstanding = total_outstanding / total if total > 0 else 0

        # Group by days since start
        by_days = {}
        for d in defaulters:
            days = d.days_since_start
            if days not in by_days:
                by_days[days] = []
            by_days[days].append(d)

        return {
            "total_defaulters": total,
            "total_outstanding": total_outstanding,
            "average_outstanding_per_defaulter": avg_outstanding,
            "breakdown_by_days": {
                str(days): {
                    "count": len(loans),
                    "total_outstanding": sum(l.remaining_amount for l in loans),
                }
                for days, loans in sorted(by_days.items())
            },
        }
