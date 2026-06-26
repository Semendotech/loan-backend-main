

# ============ AUTH MODELS ============
class UserRole(PyEnum):
    ADMIN = "ADMIN"
    USER = "USER"

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    role = Column(Enum(UserRole), default=UserRole.USER)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

"""
CORRECTED Models - Loan Management System
- Added is_defaulter flag to Loan model
- Cleaned up status definitions
- Ensured Arrears properly tracks overdue loans
"""

from datetime import datetime, timedelta
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Enum, ForeignKey, func
from sqlalchemy.orm import relationship
from enum import Enum as PyEnum
from app.database import Base

class LoanStatus(PyEnum):
    """
    Loan Status Definition:
    - ACTIVE: Days 1-30 from creation (repayment in progress)
    - OVERDUE: Day 31+ from creation (not cleared within 30 days)
    - COMPLETED: remaining_amount = 0 (fully paid)
    """
    ACTIVE = "ACTIVE"
    OVERDUE = "OVERDUE"
    COMPLETED = "COMPLETED"


class Loan(Base):
    __tablename__ = "loans"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(String, ForeignKey("customers.id_number"), nullable=False, index=True)
    guarantor_id = Column(String, nullable=True)
    amount = Column(Float, nullable=False)  # Principal amount
    interest_rate = Column(Float, default=20.0, nullable=False)  # Always 20%
    total_amount = Column(Float, nullable=False)  # Principal + Interest
    remaining_amount = Column(Float, nullable=False)  # Balance owed
    
    # Date tracking
    start_date = Column(DateTime, default=datetime.utcnow, nullable=False)  # Creation date
    due_date = Column(DateTime, nullable=False)  # start_date + 30 days (EXACT)
    completed_at = Column(DateTime, nullable=True)  # When remaining_amount reaches 0
    
    # Status tracking
    status = Column(
        Enum(LoanStatus),
        default=LoanStatus.ACTIVE,
        nullable=False,
        index=True
    )
    
    # Defaulter flag (CRITICAL)
    # True if: during ACTIVE period, failed to pay >= (daily_instalment * 5) in any 5 consecutive days
    is_defaulter = Column(Boolean, default=False, nullable=False, index=True)
    defaulter_flagged_date = Column(DateTime, nullable=True)  # When flagged as defaulter
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    customer = relationship("Customer", back_populates="loans")
    installments = relationship("Installment", back_populates="loan", cascade="all, delete-orphan")
    arrears = relationship("Arrears", back_populates="loan", uselist=False, cascade="all, delete-orphan")

    # Computed properties
    @property
    def days_since_start(self):
        """Days elapsed since loan creation"""
        return (datetime.utcnow() - self.start_date).days

    @property
    def daily_instalment(self):
        """Daily payment amount (30 equal payments)"""
        return self.total_amount / 30

    @property
    def is_active_period(self):
        """True if loan is within 30-day active period (days 1-30)"""
        return self.days_since_start <= 30 and self.days_since_start >= 0

    @property
    def is_overdue_by_days(self):
        """True if loan is past 30 days (day 31+)"""
        return self.days_since_start >= 31

    @property
    def status_should_be(self):
        """
        Compute what status SHOULD be based on days and remaining amount.
        Used to detect and fix status drift.
        """
        if self.remaining_amount <= 0:
            return LoanStatus.COMPLETED
        elif self.days_since_start >= 31:
            return LoanStatus.OVERDUE
        else:
            return LoanStatus.ACTIVE


class Arrears(Base):
    """
    ARREARS = Unpaid balance on overdue loans (day 31+)
    
    Represents:
    - Loans that have exceeded 30-day repayment window
    - Tracks remaining amount separately (should match Loan.remaining_amount)
    - is_cleared = false means loan is still overdue
    - is_cleared = true means overdue loan has been fully paid
    """
    __tablename__ = "arrears"

    id = Column(Integer, primary_key=True, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id"), unique=True, nullable=False, index=True)
    customer_id = Column(String, ForeignKey("customers.id_number"), nullable=False, index=True)
    
    # Amount tracking
    original_amount = Column(Float, nullable=False)  # Amount owed when arrears record created
    remaining_amount = Column(Float, nullable=False)  # Current unpaid amount (should sync with Loan.remaining_amount)
    
    # Status
    is_cleared = Column(Boolean, default=False, nullable=False, index=True)
    
    # Date tracking
    arrears_date = Column(DateTime, default=datetime.utcnow, nullable=False)  # When loan became overdue (day 31)
    cleared_date = Column(DateTime, nullable=True)  # When fully paid
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    loan = relationship("Loan", back_populates="arrears")
    customer = relationship("Customer", back_populates="arrears")


class Installment(Base):
    """
    Installment = Individual payment records
    Tracks each payment toward the loan
    """
    __tablename__ = "installments"

    id = Column(Integer, primary_key=True, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id"), nullable=False, index=True)
    
    amount = Column(Float, nullable=False)  # Amount paid
    payment_date = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    payment_method = Column(String, nullable=True)  # e.g., "MPESA", "CASH", "BANK"
    
    reference_number = Column(String, nullable=True, unique=True)  # M-Pesa ref or bank ref
    notes = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    loan = relationship("Loan", back_populates="installments")


class Customer(Base):
    """Customer model (unchanged structure)"""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    id_number = Column(String, unique=True, nullable=False, index=True)  # National ID
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    phone_number = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=True)
    
    guarantor_name = Column(String, nullable=True)
    guarantor_phone = Column(String, nullable=True)
    
    address = Column(String, nullable=True)
    occupation = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    loans = relationship("Loan", back_populates="customer", cascade="all, delete-orphan")
    arrears = relationship("Arrears", back_populates="customer", cascade="all, delete-orphan")

    @property
    def has_active_loan(self):
        """True if customer has any ACTIVE status loan"""
        return any(loan.status == LoanStatus.ACTIVE for loan in self.loans)

    @property
    def active_loan_count(self):
        """Count of ACTIVE loans only"""
        return sum(1 for loan in self.loans if loan.status == LoanStatus.ACTIVE)

    @property
    def overdue_loan_count(self):
        """Count of OVERDUE loans"""
        return sum(1 for loan in self.loans if loan.status == LoanStatus.OVERDUE)

    @property
    def completed_loan_count(self):
        """Count of COMPLETED loans"""
        return sum(1 for loan in self.loans if loan.status == LoanStatus.COMPLETED)


class DefaulterFlag(Base):
    """
    Audit log for defaulter status changes
    Helps track when/why a loan was flagged or cleared as defaulter
    """
    __tablename__ = "defaulter_flags"

    id = Column(Integer, primary_key=True, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id"), nullable=False, index=True)
    customer_id = Column(String, ForeignKey("customers.id_number"), nullable=False, index=True)
    
    action = Column(String, nullable=False)  # "FLAGGED" or "CLEARED"
    reason = Column(String, nullable=False)  # Explanation
    
    # Payment details that triggered the flag
    days_checked = Column(Integer, nullable=False)  # e.g., 5 (last 5 days)
    required_amount = Column(Float, nullable=False)  # Amount that should have been paid
    actual_amount = Column(Float, nullable=False)  # Amount actually paid
    
    checked_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    loan = relationship("Loan")
    customer = relationship("Customer")
