from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date
from enum import Enum


# ----------------------------------------------------
# AUTH SCHEMAS
# ----------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


# ----------------------------------------------------
# USER SCHEMAS
# ----------------------------------------------------
class UserRoleEnum(str, Enum):
    ADMIN = "admin"
    LOAN_OFFICER = "loan_officer"


class UserCreate(BaseModel):
    username: str
    password: str
    first_name: Optional[str] = None
    role: UserRoleEnum = UserRoleEnum.LOAN_OFFICER


class SignupRequest(BaseModel):
    username: str
    password: str
    first_name: Optional[str] = None


class UserUpdate(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    first_name: Optional[str] = None
    role: Optional[UserRoleEnum] = None


class UserResponse(BaseModel):
    id: int
    username: str
    first_name: Optional[str] = None
    role: UserRoleEnum
    created_at: datetime

    class Config:
        from_attributes = True


class RoleResponse(BaseModel):
    name: str
    description: str


# ----------------------------------------------------
# ENUMS
# ----------------------------------------------------
class LoanStatusEnum(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    OVERDUE = "overdue"
    ARREARS = "arrears"


# ----------------------------------------------------
# CUSTOMER SCHEMAS
# ----------------------------------------------------
class CustomerBase(BaseModel):
    name: str
    id_number: str
    phone: str
    location: Optional[str] = None
    profile_image_url: Optional[str] = None


class CustomerCreate(CustomerBase):
    pass


class CustomerResponse(CustomerBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class CustomerPhotoUpdate(BaseModel):
    profile_image_url: str


class CustomerUpdate(BaseModel):
    """Admin-only update of a customer's phone number and/or ID number."""
    id_number: Optional[str] = None
    phone: Optional[str] = None


class CustomerCheckRequest(BaseModel):
    customer_id: Optional[int] = None
    id_number: Optional[str] = None


class CustomerCheck(BaseModel):
    exists: bool
    has_active_loan: bool
    has_overdue_loans: bool
    customer: Optional[CustomerResponse] = None

    class Config:
        from_attributes = True


# ----------------------------------------------------
# GUARANTOR SCHEMAS
# ----------------------------------------------------
class GuarantorBase(BaseModel):
    name: str
    id_number: str
    phone: str
    location: Optional[str] = None
    relationship: Optional[str] = None


class GuarantorCreate(GuarantorBase):
    pass


class GuarantorResponse(GuarantorBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


# ----------------------------------------------------
# LOAN SCHEMAS
# ----------------------------------------------------
class LoanBase(BaseModel):
    id_number: str
    amount: float
    interest_rate: float
    start_date: date


class LoanCreate(LoanBase):
    guarantor: Optional[GuarantorCreate] = None


class LoanUpdate(BaseModel):
    amount: Optional[float] = None
    interest_rate: Optional[float] = None
    start_date: Optional[date] = None
    due_date: Optional[date] = None


class GuarantorUpdate(BaseModel):
    name: Optional[str] = None
    id_number: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    relationship: Optional[str] = None


class LoanResponse(BaseModel):
    id: int
    customer_id: str   # ✅ match the DB column
    guarantor_id: Optional[int] = None
    amount: float
    interest_rate: float
    total_amount: float
    start_date: date
    due_date: date
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    guarantor: Optional[GuarantorResponse] = None
    document_url: Optional[str] = None

    class Config:
        from_attributes = True


# ----------------------------------------------------
# INSTALLMENT SCHEMAS
# ----------------------------------------------------
class InstallmentResponse(BaseModel):
    id: int
    loan_id: int
    amount: float
    payment_date: datetime
    created_at: datetime

    class Config:
        from_attributes = True


# ----------------------------------------------------
# ARREARS SCHEMAS
# ----------------------------------------------------
class ArrearsResponse(BaseModel):
    id: int
    loan_id: int
    customer_id: int
    original_amount: float
    remaining_amount: float
    arrears_date: date
    is_cleared: bool
    cleared_date: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True
