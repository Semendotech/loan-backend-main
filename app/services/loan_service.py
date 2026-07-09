"""
CORRECTED Loan Service - Core Business Logic
- Proper defaulter detection (5 consecutive days < required amount)
- Status sync (ACTIVE → OVERDUE on day 31)
- Arrears creation and syncing
- Daily operations
"""

from datetime import datetime, timedelta, date
from app.utils.timezone import now_eat
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select as sa_select
from sqlalchemy import func, and_, or_
from app.models import Loan, Arrears, Installment, Customer, LoanStatus, DefaulterFlag


class LoanService:
    """Core loan business logic"""

    @staticmethod
    def create_loan(
        db: Session,
        customer_id: str,
        amount: float,
        guarantor_id: int = None,
        interest_rate: float = 20.0,
        start_date=None,
    ) -> Loan:
        """
        Create a new loan.
        
        Business Rules:
        - Status starts as ACTIVE
        - due_date = start_date + exactly 30 days
        - total_amount = amount + (amount * interest_rate / 100)
        - is_defaulter = False initially
        """
        from datetime import date as _date
        if start_date is None:
            start_date = _date.today()
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
            loan.updated_at = now_eat()
            status_changed = True

        # If transitioning to OVERDUE, create Arrears record
        if expected_status == LoanStatus.OVERDUE and not loan.arrears:
            LoanService.create_arrears_record(db, loan)

        # If transitioning to COMPLETED, mark arrears as cleared
        if expected_status == LoanStatus.COMPLETED:
            if loan.arrears:
                loan.arrears.is_cleared = True
                loan.arrears.cleared_date = now_eat()
                loan.arrears.remaining_amount = 0
            loan.completed_at = now_eat()

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

        from app.models import Customer
        customer = db.query(Customer).filter(Customer.id_number == loan.customer_id).first()
        if not customer:
            raise ValueError(f"Customer with id_number {loan.customer_id!r} not found for loan {loan.id}")

        arrears = Arrears(
            loan_id=loan.id,
            customer_id=customer.id,
            original_amount=loan.remaining_amount,
            remaining_amount=loan.remaining_amount,
            is_cleared=False,
            arrears_date=now_eat(),
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
            loan.arrears.updated_at = now_eat()
            db.commit()

    @staticmethod
    def check_defaulter_status(db: Session, loan_id: int) -> bool:
        """
        Check if loan should be flagged as DEFAULTER.

        Rule: DEFAULTER if in ACTIVE period (days 1-30) AND
              no payment made in the last 5 consecutive days.

        Returns True if flagged, False otherwise.
        """
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

    def record_payment(db: Session, loan_id: int, amount: float, payment_method: str = None, reference: str = None, recorded_by: str = None) -> Installment:
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
        # Reduce remaining amount first so balance_after is correct
        loan.remaining_amount -= amount
        if loan.remaining_amount < 0:
            loan.remaining_amount = 0

        installment = Installment(
            loan_id=loan_id,
            amount=amount,
            payment_date=now_eat(),
            payment_method=payment_method,
            reference_number=reference,
            recorded_by=recorded_by,
            balance_after=loan.remaining_amount,
        )
        db.add(installment)

        loan.updated_at = now_eat()

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
            Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.OVERDUE, LoanStatus.ARREARS])
        ).all()

        for loan in loans:
            # Sync status (will create Arrears if becoming OVERDUE)
            LoanService.sync_loan_status(db, loan)

            # Check defaulter status if ACTIVE
            if loan.is_active_period:
                LoanService.check_defaulter_status(db, loan_id=loan.id)

        db.commit()

    @staticmethod
    def get_active_loans(db: Session, limit: int = 50, offset: int = 0, search: str = "") -> tuple[list, int]:
        """
        Get ACTIVE loans (days 1-30 from creation).
        Filter: status == ACTIVE AND (today - start_date).days <= 30
        Returns: (loans list, total count)
        """
        from sqlalchemy.orm import selectinload
        from app.models import Customer as _Customer
        query = db.query(Loan).options(selectinload(Loan.customer)).join(Loan.customer).filter(
            Loan.status == LoanStatus.ACTIVE,
            func.datediff(func.now(), Loan.start_date) <= 30,
        )
        if search:
            like = f"%{search}%"
            query = query.filter(
                (_Customer.name.ilike(like)) |
                (_Customer.id_number.ilike(like)) |
                (_Customer.phone.ilike(like))
            )
        total = query.count()
        loans = query.limit(limit).offset(offset).all()
        return loans, total

    @staticmethod
    def get_payable_loans(db: Session, limit: int = 50, offset: int = 0, search: str = "") -> tuple[list, int]:
        """
        Get all loans with an outstanding balance for the Pay Installments page.
        Includes ACTIVE, OVERDUE, and ARREARS statuses (anything still owed).
        Returns: (loans list, total count)
        """
        from sqlalchemy.orm import selectinload
        from app.models import Customer as _Customer
        query = db.query(Loan).options(selectinload(Loan.customer)).join(Loan.customer).filter(
            Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.OVERDUE, LoanStatus.ARREARS]),
            Loan.remaining_amount > 0,
        )
        if search:
            like = f"%{search}%"
            query = query.filter(
                (_Customer.name.ilike(like)) |
                (_Customer.id_number.ilike(like)) |
                (_Customer.phone.ilike(like))
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
        from sqlalchemy.orm import selectinload
        query = db.query(Loan).options(selectinload(Loan.customer)).filter(Loan.status == LoanStatus.OVERDUE)

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
        from sqlalchemy.orm import selectinload
        query = db.query(Loan).options(selectinload(Loan.customer)).filter(Loan.status == LoanStatus.COMPLETED)

        total = query.count()
        loans = query.limit(limit).offset(offset).all()

        return loans, total

    @staticmethod
    @staticmethod
    def get_loan_dashboard_metrics(db: Session) -> dict:
        """Get dashboard metrics efficiently using combined queries."""
        import time
        start = time.time()
        
        # Query 1: ACTIVE loans
        active_result = db.query(
            func.count(Loan.id).label("count"),
            func.sum(Loan.remaining_amount).label("sum")
        ).filter(Loan.status == LoanStatus.ACTIVE).first()
        
        active_loans_count = active_result.count or 0
        active_loans_outstanding = float(active_result.sum) if active_result.sum else 0.0
        
        # Query 2: OVERDUE loans
        overdue_result = db.query(
            func.count(Arrears.id).label("count"),
            func.sum(Arrears.remaining_amount).label("sum")
        ).filter(Arrears.is_cleared == False).first()
        
        overdue_loans_count = overdue_result.count or 0
        overdue_outstanding = float(overdue_result.sum) if overdue_result.sum else 0.0
        
        # Query 3: DEFAULTERS and COMPLETED
        defaulters_result = db.query(func.count(Loan.id).label("count")).filter(
            Loan.is_defaulter == True, Loan.status == LoanStatus.ACTIVE
        ).first()
        
        completed_result = db.query(
            func.count(Loan.id).label("count"),
            func.sum(Loan.total_amount).label("sum")
        ).filter(Loan.status == LoanStatus.COMPLETED).first()
        
        defaulters_count = defaulters_result.count or 0
        completed_count = completed_result.count or 0
        completed_outstanding = float(completed_result.sum) if completed_result.sum else 0.0
        
        elapsed = time.time() - start
        print(f">>> DASHBOARD METRICS took {elapsed:.3f}s", flush=True)
        
        return {
            "active_loans": active_loans_count,
            "active_loans_outstanding": active_loans_outstanding,
            "overdue_loans": overdue_loans_count,
            "overdue_outstanding": overdue_outstanding,
            "defaulters": defaulters_count,
            "completed_loans": completed_count,
            "completed_cleared_amount": completed_outstanding,
        }


def compute_weekly_progress(loan, reference_date=None):
    """
    Calculate how much should have been paid for the current week-based schedule
    and the arrears accumulated so far.
    """
    TOTAL_WEEKS = 4

    total_amount = float(loan.total_amount) if loan.total_amount is not None else 0.0
    remaining_amount = float(loan.remaining_amount) if loan.remaining_amount is not None else total_amount
    actual_paid = total_amount - remaining_amount

    if not loan.start_date:
        return {
            "weekly_due_amount": 0.0,
            "weeks_elapsed": 0,
            "expected_paid": 0.0,
            "actual_paid": actual_paid,
            "arrears_amount": 0.0,
        }

    ref_date = reference_date or now_eat().date()
    start_date = loan.start_date
    if hasattr(start_date, "date"):
        start_date = start_date.date()

    weekly_due_amount = round(total_amount / TOTAL_WEEKS, 2)

    days_elapsed = (ref_date - start_date).days
    weeks_elapsed = 0
    if days_elapsed >= 0:
        weeks_elapsed = min(TOTAL_WEEKS, (days_elapsed // 7) + 1)

    expected_paid = weekly_due_amount * weeks_elapsed
    arrears_amount = max(0.0, round(expected_paid - actual_paid, 2))

    return {
        "weekly_due_amount": weekly_due_amount,
        "weeks_elapsed": weeks_elapsed,
        "expected_paid": round(expected_paid, 2),
        "actual_paid": round(actual_paid, 2),
        "arrears_amount": arrears_amount,
    }


def loan_is_overdue_by_schedule(loan, reference_date=None):
    """
    Determine if a loan is overdue based on its due_date, independent of
    whatever status is currently persisted on the row.
    """
    ref_date = reference_date or now_eat().date()
    if not loan.due_date:
        return False

    due_date = loan.due_date
    if hasattr(due_date, "date"):
        due_date = due_date.date()

    remaining_amount = loan.remaining_amount
    if remaining_amount is None:
        remaining_amount = loan.total_amount or 0.0

    return due_date < ref_date and float(remaining_amount) > 0


async def sync_overdue_state(db: AsyncSession, loan) -> bool:
    """
    Ensure the loan status/arrears record reflects whether it is overdue.
    Returns True if any mutation occurred.
    """
    from sqlalchemy.orm import selectinload

    ref_date = now_eat().date()
    remaining_amount = loan.remaining_amount
    if remaining_amount is None:
        remaining_amount = loan.total_amount or 0.0
    remaining_amount = float(remaining_amount)

    changed = False
    is_overdue = loan_is_overdue_by_schedule(loan, ref_date)

    if is_overdue:
        if loan.status != LoanStatus.OVERDUE:
            loan.status = LoanStatus.OVERDUE
            loan.updated_at = now_eat()
            changed = True

        result = await db.execute(sa_select(Arrears).filter(Arrears.loan_id == loan.id))
        arrears = result.scalar_one_or_none()

        if not arrears:
            arrears = Arrears(
                loan_id=loan.id,
                customer_id=loan.customer_id,
                original_amount=loan.total_amount,
                remaining_amount=remaining_amount,
                is_cleared=False,
                arrears_date=now_eat(),
            )
            db.add(arrears)
            changed = True
        else:
            if abs((arrears.remaining_amount or 0.0) - remaining_amount) > 0.01:
                arrears.remaining_amount = remaining_amount
                changed = True
            if arrears.is_cleared:
                arrears.is_cleared = False
                arrears.cleared_date = None
                changed = True
    else:
        if loan.status == LoanStatus.OVERDUE and remaining_amount <= 0:
            loan.status = LoanStatus.COMPLETED
            loan.completed_at = now_eat()
            changed = True

        result = await db.execute(sa_select(Arrears).filter(Arrears.loan_id == loan.id))
        arrears = result.scalar_one_or_none()
        if arrears and not arrears.is_cleared and remaining_amount <= 0:
            arrears.remaining_amount = 0.0
            arrears.is_cleared = True
            arrears.cleared_date = now_eat()
            changed = True

    if changed:
        await db.commit()

    return changed
