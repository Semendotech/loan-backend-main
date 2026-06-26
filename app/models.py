"""
CORRECTED Models - Loan Management System
"""
from datetime import datetime, timedelta
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Enum, ForeignKey, func
from sqlalchemy.orm import relationship
from enum import Enum as PyEnum
from app.database import Base

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

# ============ LOAN STATUS ============

class LoanStatus(PyEnum):
    ACTIVE = "ACTIVE"
    OVERDUE = "OVERDUE"
    COMPLETED = "COMPLETED"

# ============ LOAN MODELS ============

class Loan(Base):
    __tablename__ = "loans"
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(String, ForeignKey("customers.id_number"), nullable=False, index=True)
    guarantor_id = Column(String, nullable=True)
    amount = Column(Float, nullable=False)
    interest_rate = Column(Float, default=20.0, nullable=False)
    total_amount = Column(Float, nullable=False)
    remaining_amount = Column(Float, nullable=False)
    start_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    due_date = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    status = Column(Enum(LoanStatus), default=LoanStatus.ACTIVE, nullable=False, index=True)
    is_defaulter = Column(Boolean, default=False, nullable=False, index=True)
    defaulter_flagged_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    customer = relationship("Customer", back_populates="loans")
    installments = relationship("Installment", back_populates="loan", cascade="all, delete-orphan")
    arrears = relationship("Arrears", back_populates="loan", uselist=False, cascade="all, delete-orphan")

    @property
    def days_since_start(self):
        return (datetime.utcnow() - self.start_date).days

    @property
    def daily_instalment(self):
        return self.total_amount / 30

    @property
    def is_active_period(self):
        return self.days_since_start <= 30 and self.days_since_start >= 0

    @property
    def is_overdue_by_days(self):
        return self.days_since_start >= 31

    @property
    def status_should_be(self):
        if self.remaining_amount <= 0:
            return LoanStatus.COMPLETED
        elif self.days_since_start >= 31:
            return LoanStatus.OVERDUE
        else:
            return LoanStatus.ACTIVE

class Arrears(Base):
    __tablename__ = "arrears"
    id = Column(Integer, primary_key=True, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id"), unique=True, nullable=False, index=True)
    customer_id = Column(String, ForeignKey("customers.id_number"), nullable=False, index=True)
    original_amount = Column(Float, nullable=False)
    remaining_amount = Column(Float, nullable=False)
    is_cleared = Column(Boolean, default=False, nullable=False, index=True)
    arrears_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    cleared_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    loan = relationship("Loan", back_populates="arrears")
    customer = relationship("Customer", back_populates="arrears")

class Installment(Base):
    __tablename__ = "installments"
    id = Column(Integer, primary_key=True, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    payment_date = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    payment_method = Column(String, nullable=True)
    reference_number = Column(String, nullable=True, unique=True)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    loan = relationship("Loan", back_populates="installments")

class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True, index=True)
    id_number = Column(String, unique=True, nullable=False, index=True)
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
    loans = relationship("Loan", back_populates="customer", cascade="all, delete-orphan")
    arrears = relationship("Arrears", back_populates="customer", cascade="all, delete-orphan")

    @property
    def has_active_loan(self):
        return any(loan.status == LoanStatus.ACTIVE for loan in self.loans)

    @property
    def active_loan_count(self):
        return sum(1 for loan in self.loans if loan.status == LoanStatus.ACTIVE)

    @property
    def overdue_loan_count(self):
        return sum(1 for loan in self.loans if loan.status == LoanStatus.OVERDUE)

    @property
    def completed_loan_count(self):
        return sum(1 for loan in self.loans if loan.status == LoanStatus.COMPLETED)

class DefaulterFlag(Base):
    __tablename__ = "defaulter_flags"
    id = Column(Integer, primary_key=True, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id"), nullable=False, index=True)
    customer_id = Column(String, ForeignKey("customers.id_number"), nullable=False, index=True)
    action = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    days_checked = Column(Integer, nullable=False)
    required_amount = Column(Float, nullable=False)
    actual_amount = Column(Float, nullable=False)
    checked_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    loan = relationship("Loan")
    customer = relationship("Customer")
