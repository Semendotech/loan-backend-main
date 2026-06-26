"""
CORRECTED Loan Service - Core Business Logic
- Proper defaulter detection (5 consecutive days < required amount)
- Status sync (ACTIVE → OVERDUE on day 31)
- Arrears creation and syncing
- Daily operations
"""

from datetime import datetime, timedelta, date
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from app.models import Loan, Arrears, Installment, Customer, LoanStatus, DefaulterFlag


class LoanService:
    """Core loan business logic"""

    @staticmethod
    def create_loan(
        db: Session,
        customer_id: str,
        amount: float,
        guarantor_id: str = None,
        interest_rate: float = 20.0
    ) -> Loan:
        """
        Create a new loan.
        
        Business Rules:
        - Status starts as ACTIVE
        - due_date = start_date + exactly 30 days
        - total_amount = amount + (amount * interest_rate / 100)
        - is_defaulter = False initially
        """
        start_date = datetime.utcnow()
        due_date = start_date + timedelta(days=30)
        total_amount = amount * (1 + interest_rate / 100)

        loan = Loan(
            customer_id=customer_id,
            guarantor_id=guarantor_id,
            amount=amount,
            interest_rate=interest_rate,
            total_amount=total_amount,
            remaining_amount=total_amount,
            start_date=start_date,
            due_date=due_date,
            status=LoanStatus.ACTIVE,
            is_defaulter=False,
        )
        db.add(loan)
        db.commit()
        db.refresh(loan)
        return loan

    @staticmethod
    def sync_loan_status(db: Session, loan: Loan) -> bool:
        """
        Sync loan status based on days elapsed and remaining balance.
        
        Logic:
        - If remaining_amount <= 0 → COMPLETED
        - If days_since_start >= 31 → OVERDUE (create Arrears if not exists)
        - Otherwise → ACTIVE
        
        Returns True if status changed, False otherwise.
        """
        expected_status = loan.status_should_be
        status_changed = False

        if loan.status != expected_status:
            loan.status = expected_status
            loan.updated_at = datetime.utcnow()
            status_changed = True

        # If transitioning to OVERDUE, create Arrears record
        if expected_status == LoanStatus.OVERDUE and not loan.arrears:
            LoanService.create_arrears_record(db, loan)

        # If transitioning to COMPLETED, mark arrears as cleared
        if expected_status == LoanStatus.COMPLETED:
            if loan.arrears:
                loan.arrears.is_cleared = True
                loan.arrears.cleared_date = datetime.utcnow()
                loan.arrears.remaining_amount = 0
            loan.completed_at = datetime.utcnow()

        db.commit()
        return status_changed

    @staticmethod
    def create_arrears_record(db: Session, loan: Loan) -> Arrears:
        """
        Create Arrears record when loan becomes OVERDUE (day 31+).
        
        Arrears = Unpaid balance on overdue loans
        """
        if loan.arrears:
            return loan.arrears  # Already exists

        arrears = Arrears(
            loan_id=loan.id,
            customer_id=loan.customer_id,
            original_amount=loan.remaining_amount,
            remaining_amount=loan.remaining_amount,
            is_cleared=False,
            arrears_date=datetime.utcnow(),
        )
        db.add(arrears)
        db.commit()
        db.refresh(arrears)
        return arrears

    @staticmethod
    def sync_arrears_balance(db: Session, loan: Loan):
        """
        Sync Arrears.remaining_amount with Loan.remaining_amount.
        Called after every payment to keep them in sync.
        """
        if loan.arrears:
            loan.arrears.remaining_amount = loan.remaining_amount
            loan.arrears.updated_at = datetime.utcnow()
            db.commit()

    @staticmethod
    def check_defaulter_status(db: Session, loan_id: int) -> bool:
        """
        Check if loan should be flagged as DEFAULTER.
        
        Rule: DEFAULTER if in ACTIVE period (days 1-30) AND
              sum of payments in any 5 consecutive days < (daily_instalment * 5)
        
        Returns True if flagged, False otherwise.
        """
        loan = db.query(Loan).filter(Loan.id == loan_id).first()
        if not loan:
            return False

        # Only check during ACTIVE period
        if not loan.is_active_period:
            return False

        today = datetime.utcnow().date()
        daily_instalment = loan.daily_instalment
        required_5_day_amount = daily_instalment * 5

        # Check last 5 consecutive days
        five_days_ago = datetime.utcnow() - timedelta(days=5)

        payments_last_5_days = db.query(func.sum(Installment.amount)).filter(
            Installment.loan_id == loan_id,
            Installment.payment_date >= five_days_ago,
            Installment.payment_date <= datetime.utcnow(),
        ).scalar()

        actual_amount = payments_last_5_days or 0

        is_defaulter = actual_amount < required_5_day_amount

        # Update loan if status changed
        if is_defaulter and not loan.is_defaulter:
            loan.is_defaulter = True
            loan.defaulter_flagged_date = datetime.utcnow()

            # Log the flag
            flag_record = DefaulterFlag(
                loan_id=loan_id,
                customer_id=loan.customer_id,
                action="FLAGGED",
                reason=f"Payment in last 5 days ({actual_amount}) < required ({required_5_day_amount})",
                days_checked=5,
                required_amount=required_5_day_amount,
                actual_amount=actual_amount,
            )
            db.add(flag_record)
            db.commit()
            return True

        elif not is_defaulter and loan.is_defaulter:
            # Clear the defaulter flag if they catch up
            loan.is_defaulter = False

            # Log the clear
            flag_record = DefaulterFlag(
                loan_id=loan_id,
                customer_id=loan.customer_id,
                action="CLEARED",
                reason=f"Payment in last 5 days ({actual_amount}) >= required ({required_5_day_amount})",
                days_checked=5,
                required_amount=required_5_day_amount,
                actual_amount=actual_amount,
            )
            db.add(flag_record)
            db.commit()
            return False

        db.commit()
        return is_defaulter

    @staticmethod
    def record_payment(db: Session, loan_id: int, amount: float, payment_method: str = None, reference: str = None) -> Installment:
        """
        Record a payment against a loan.
        
        Process:
        1. Reduce Loan.remaining_amount
        2. Sync Arrears.remaining_amount
        3. Check and update defaulter status
        4. Sync loan status (might become COMPLETED)
        5. Create installment record
        """
        loan = db.query(Loan).filter(Loan.id == loan_id).first()
        if not loan:
            raise ValueError(f"Loan {loan_id} not found")

        # Record the installment
        installment = Installment(
            loan_id=loan_id,
            amount=amount,
            payment_date=datetime.utcnow(),
            payment_method=payment_method,
            reference_number=reference,
        )
        db.add(installment)

        # Reduce remaining amount
        loan.remaining_amount -= amount
        if loan.remaining_amount < 0:
            loan.remaining_amount = 0

        loan.updated_at = datetime.utcnow()

        # Sync arrears balance
        LoanService.sync_arrears_balance(db, loan)

        # Check and update defaulter status (only during ACTIVE period)
        if loan.is_active_period:
            LoanService.check_defaulter_status(db, loan_id)

        # Sync loan status (might become COMPLETED or OVERDUE)
        LoanService.sync_loan_status(db, loan)

        db.commit()
        db.refresh(installment)
        return installment

    @staticmethod
    def daily_sync_all_loans(db: Session):
        """
        Run daily to sync all loan statuses.
        - Mark loans that hit day 31 as OVERDUE
        - Create Arrears records for them
        - Check defaulter status for all ACTIVE loans
        """
        # Get all non-completed loans
        loans = db.query(Loan).filter(
            Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.OVERDUE])
        ).all()

        for loan in loans:
            # Sync status (will create Arrears if becoming OVERDUE)
            LoanService.sync_loan_status(db, loan)

            # Check defaulter status if ACTIVE
            if loan.is_active_period:
                LoanService.check_defaulter_status(db, loan_id=loan.id)

        db.commit()

    @staticmethod
    def get_active_loans(db: Session, limit: int = 50, offset: int = 0) -> tuple[list, int]:
        """
        Get ACTIVE loans (days 1-30 from creation).
        
        Filter: (today - start_date).days <= 30 AND status == ACTIVE
        
        Returns: (loans list, total count)
        """
        query = db.query(Loan).filter(
            Loan.status == LoanStatus.ACTIVE,
            (func.julianday('now') - func.julianday(Loan.start_date)) <= 30,
        )

        total = query.count()
        loans = query.limit(limit).offset(offset).all()

        return loans, total

    @staticmethod
    def get_overdue_loans(db: Session, limit: int = 50, offset: int = 0) -> tuple[list, int]:
        """
        Get OVERDUE loans (day 31+ from creation).
        
        Filter: status == OVERDUE
        
        Returns: (loans list, total count)
        """
        query = db.query(Loan).filter(Loan.status == LoanStatus.OVERDUE)

        total = query.count()
        loans = query.limit(limit).offset(offset).all()

        return loans, total

    @staticmethod
    def get_defaulters(db: Session, limit: int = 50, offset: int = 0) -> tuple[list, int]:
        """
        Get DEFAULTER loans.
        
        Filter: is_defaulter == True AND status == ACTIVE (only tracked during active period)
        
        Returns: (loans list, total count)
        """
        query = db.query(Loan).filter(
            Loan.is_defaulter == True,
            Loan.status == LoanStatus.ACTIVE,
        )

        total = query.count()
        loans = query.limit(limit).offset(offset).all()

        return loans, total

    @staticmethod
    def get_completed_loans(db: Session, limit: int = 50, offset: int = 0) -> tuple[list, int]:
        """
        Get COMPLETED loans (fully paid).
        
        Filter: status == COMPLETED
        
        Returns: (loans list, total count)
        """
        query = db.query(Loan).filter(Loan.status == LoanStatus.COMPLETED)

        total = query.count()
        loans = query.limit(limit).offset(offset).all()

        return loans, total

    @staticmethod
    def get_loan_dashboard_metrics(db: Session) -> dict:
        """
        Get dashboard metrics based on correct definitions.
        
        Returns:
        {
            "active_loans": count of ACTIVE loans,
            "active_loans_outstanding": sum of remaining for ACTIVE loans,
            "overdue_loans": count of OVERDUE loans (or Arrears.is_cleared == false),
            "overdue_outstanding": sum of remaining for OVERDUE loans,
            "defaulters": count of is_defaulter == true,
            "completed_loans": count of COMPLETED loans,
            "total_cleared_amount": sum of completed loan total_amounts,
        }
        """
        # ACTIVE loans
        active_loans_count = db.query(func.count(Loan.id)).filter(
            Loan.status == LoanStatus.ACTIVE
        ).scalar()

        active_loans_outstanding = db.query(func.sum(Loan.remaining_amount)).filter(
            Loan.status == LoanStatus.ACTIVE
        ).scalar() or 0

        # OVERDUE loans (same as Arrears with is_cleared = false)
        overdue_loans_count = db.query(func.count(Arrears.id)).filter(
            Arrears.is_cleared == False
        ).scalar()

        overdue_outstanding = db.query(func.sum(Arrears.remaining_amount)).filter(
            Arrears.is_cleared == False
        ).scalar() or 0

        # DEFAULTERS (subset of ACTIVE)
        defaulters_count = db.query(func.count(Loan.id)).filter(
            Loan.is_defaulter == True,
            Loan.status == LoanStatus.ACTIVE,
        ).scalar()

        # COMPLETED loans
        completed_count = db.query(func.count(Loan.id)).filter(
            Loan.status == LoanStatus.COMPLETED
        ).scalar()

        completed_outstanding = db.query(func.sum(Loan.total_amount)).filter(
            Loan.status == LoanStatus.COMPLETED
        ).scalar() or 0

        return {
            "active_loans": active_loans_count,
            "active_loans_outstanding": active_loans_outstanding,
            "overdue_loans": overdue_loans_count,
            "overdue_outstanding": overdue_outstanding,
            "defaulters": defaulters_count,
            "completed_loans": completed_count,
            "completed_cleared_amount": completed_outstanding,
        }
