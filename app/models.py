from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, ForeignKey, Date, Enum
from sqlalchemy.orm import relationship as orm_relationship, validates
from datetime import datetime, timedelta
import enum
from app.database import Base
from app.utils.phone import normalize_phone, hash_phone

class UserRole(enum.Enum):
    ADMIN = "admin"
    LOAN_OFFICER = "loan_officer"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    first_name = Column(String(50), nullable=True)
    role = Column(Enum(UserRole, values_callable=lambda enum_cls: [e.value for e in enum_cls]), nullable=False, default=UserRole.LOAN_OFFICER)
    created_at = Column(DateTime, default=datetime.utcnow)

class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    id_number = Column(String(30), unique=True, nullable=False)
    phone = Column(String(20), unique=True, nullable=False)
    phone_hash = Column(String(64), unique=True, index=True, nullable=True)
    location = Column(String(100))
    profile_image_url = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    loans = orm_relationship("Loan", back_populates="customer", cascade="all, delete-orphan")
    arrears = orm_relationship("Arrears", back_populates="customer", cascade="all, delete-orphan")

    @validates("phone")
    def validate_phone(self, key: str, value: str) -> str:
        """Normalize phone and compute phone_hash whenever phone is set."""
        normalized = normalize_phone(value)
        self.phone_hash = hash_phone(normalized)
        return normalized

class Guarantor(Base):
    __tablename__ = "guarantors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    id_number = Column(String(30), nullable=False)
    phone = Column(String(20), nullable=False)
    location = Column(String(100), nullable=True)
    relationship = Column(String(50), nullable=True)  # e.g., "Friend", "Family", "Colleague"
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    loans = orm_relationship("Loan", back_populates="guarantor")

class LoanStatus(enum.Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    OVERDUE = "OVERDUE"
    ARREARS = "ARREARS"

class Loan(Base):
    __tablename__ = "loans"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(String(30), ForeignKey("customers.id_number"), nullable=False)
    guarantor_id = Column(Integer, ForeignKey("guarantors.id"), nullable=True)
    amount = Column(Float, nullable=False)
    interest_rate = Column(Float, default=20.0, nullable=False)  # 20% interest rate
    total_amount = Column(Float, nullable=False)  # Principal + Interest
    # Remaining amount to be paid (initialized to total_amount)
    remaining_amount = Column(Float, nullable=True)
    start_date = Column(Date, nullable=False, default=datetime.utcnow().date)
    due_date = Column(Date, nullable=False)
    status = Column(Enum(LoanStatus), default=LoanStatus.ACTIVE, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    arrears_since = Column(DateTime, nullable=True)
    
    # Relationships
    customer = orm_relationship("Customer", back_populates="loans")
    guarantor = orm_relationship("Guarantor", back_populates="loans")
    installments = orm_relationship("Installment", back_populates="loan", cascade="all, delete-orphan")
    arrears = orm_relationship("Arrears", back_populates="loan", uselist=False, cascade="all, delete-orphan")

    def __init__(self, **kwargs):
        super(Loan, self).__init__(**kwargs)
        # Calculate total amount (principal + interest)
        if 'amount' in kwargs:
            interest = kwargs.get('amount') * (kwargs.get('interest_rate', 20.0) / 100)
            self.total_amount = kwargs.get('amount') + interest
            # Initialize remaining amount to total amount on creation
            if self.remaining_amount is None:
                self.remaining_amount = self.total_amount
        
        # Set due date (1 month from start date)
        if 'start_date' in kwargs:
            start = kwargs.get('start_date')
            # Add one month to the start date
            if isinstance(start, datetime):
                start = start.date()
            
            # Simple way to add a month (30 days)
            self.due_date = start + timedelta(days=30)
        elif not kwargs.get('due_date'):
            self.due_date = datetime.utcnow().date() + timedelta(days=30)

class Installment(Base):
    __tablename__ = "installments"

    id = Column(Integer, primary_key=True, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id"), nullable=False)
    amount = Column(Float, nullable=False)
    payment_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    recorded_by = Column(String(100), nullable=True)
    source = Column(String(30), nullable=False, default="manual")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    loan = orm_relationship("Loan", back_populates="installments")

class Arrears(Base):
    __tablename__ = "arrears"

    id = Column(Integer, primary_key=True, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id"), nullable=False, unique=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    original_amount = Column(Float, nullable=False)  # Original loan amount
    remaining_amount = Column(Float, nullable=False)  # Unpaid amount including interest
    arrears_date = Column(Date, nullable=False, default=datetime.utcnow().date)
    is_cleared = Column(Boolean, default=False)
    cleared_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    loan = orm_relationship("Loan", back_populates="arrears")
    customer = orm_relationship("Customer", back_populates="arrears")


class MpesaTransaction(Base):
    __tablename__ = "mpesa_transactions"

    id = Column(Integer, primary_key=True, index=True)
    trans_id = Column(String(100), unique=True, nullable=False)  # Safaricom transaction ID
    amount = Column(Float, nullable=False)
    phone = Column(String(20), nullable=False)  # Customer phone number
    loan_id = Column(Integer, ForeignKey("loans.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    loan = orm_relationship("Loan")