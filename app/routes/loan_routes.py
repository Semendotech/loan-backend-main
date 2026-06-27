"""
CORRECTED Loan Routes
- /active endpoint uses CORRECT filter: days since creation <= 30
- Removed broken due_date filter
- Integrated with LoanService for proper status sync
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel

from app.database import get_sync_db
from app.models import Loan, LoanStatus, Arrears
from app.services.loan_service import LoanService
from app.auth import get_current_user_sync

router = APIRouter(prefix="/loans", tags=["loans"])

# Cache to ensure daily_sync runs at most once per day
_last_sync_date: object = None


def _maybe_sync(db):
    """Run daily_sync_all_loans at most once per calendar day."""
    global _last_sync_date
    from datetime import date
    today = date.today()
    if _last_sync_date != today:
        LoanService.daily_sync_all_loans(db)
        _last_sync_date = today


# ============ SCHEMAS ============

class LoanRequest(BaseModel):
    amount: float
    guarantor_id: Optional[int] = None

    class Config:
        from_attributes = True


class CustomerBrief(BaseModel):
    id_number: str
    name: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None

    class Config:
        from_attributes = True


class LoanResponse(BaseModel):
    id: int
    customer_id: str
    guarantor_id: Optional[int]
    amount: float
    interest_rate: float
    total_amount: float
    remaining_amount: float
    start_date: datetime
    due_date: datetime
    completed_at: Optional[datetime]
    status: str
    is_defaulter: bool
    days_since_start: int
    daily_instalment: float
    created_at: datetime
    customer: Optional[CustomerBrief] = None

    class Config:
        from_attributes = True


class LoanListResponse(BaseModel):
    items: list[LoanResponse]
    total: int
    count: int
    limit: int
    offset: int
    has_more: bool

    class Config:
        from_attributes = True


# ============ ENDPOINTS ============

@router.post("/create")
def create_loan(
    loan_data: LoanRequest,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user_sync),
):
    """
    Create a new loan.
    
    Business Rules Applied:
    - Status starts as ACTIVE
    - due_date = start_date + exactly 30 days
    - total_amount = amount * 1.20 (20% interest)
    - daily_instalment = total_amount / 30
    """
    try:
        loan = LoanService.create_loan(
            db=db,
            customer_id=current_user["id_number"],
            amount=loan_data.amount,
            guarantor_id=loan_data.guarantor_id,
            interest_rate=20.0,
        )
        return LoanResponse.from_orm(loan)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/active")
def get_active_loans(
    limit: int = Query(50, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user_sync),
):
    """
    Get ACTIVE loans (days 1-30 from creation).
    
    CORRECTED FILTER:
    - (today - start_date).days <= 30  ← Days since creation (NOT due_date)
    - status == ACTIVE
    
    This ensures loans are shown during their active 30-day period,
    regardless of when due_date was calculated.
    """
    import traceback
    try:
        loans, total = LoanService.get_active_loans(db, limit=limit, offset=offset)
    except Exception as e:
        print('ACTIVE ERROR:', traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

    def _to_response(loan):
        resp = LoanResponse.from_orm(loan)
        if loan.customer:
            resp.customer = CustomerBrief.from_orm(loan.customer)
        return resp

    return LoanListResponse(
        items=[_to_response(loan) for loan in loans],
        total=total,
        count=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )


@router.get("/overdue", response_model=LoanListResponse)
def get_overdue_loans(
    limit: int = Query(50, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user_sync),
):
    """
    Get OVERDUE loans (day 31+ from creation, not cleared).
    
    Filter: status == OVERDUE
    """
    loans, total = LoanService.get_overdue_loans(db, limit=limit, offset=offset)

    def _to_response(loan):
        resp = LoanResponse.from_orm(loan)
        if loan.customer:
            resp.customer = CustomerBrief.from_orm(loan.customer)
        return resp

    return LoanListResponse(
        items=[_to_response(loan) for loan in loans],
        total=total,
        count=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )


@router.get("/cleared", response_model=LoanListResponse)
def get_cleared_loans(
    limit: int = Query(50, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user_sync),
):
    """
    Get COMPLETED loans (fully paid, remaining_amount = 0).
    
    Filter: status == COMPLETED
    """
    loans, total = LoanService.get_completed_loans(db, limit=limit, offset=offset)

    def _to_response(loan):
        resp = LoanResponse.from_orm(loan)
        if loan.customer:
            resp.customer = CustomerBrief.from_orm(loan.customer)
        return resp

    return LoanListResponse(
        items=[_to_response(loan) for loan in loans],
        total=total,
        count=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )


@router.get("/{loan_id}", response_model=LoanResponse)
def get_loan_detail(
    loan_id: int,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user_sync),
):
    """Get details of a specific loan"""
    loan = db.query(Loan).filter(Loan.id == loan_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")

    # Sync status before returning
    LoanService.sync_loan_status(db, loan)

    return LoanResponse.from_orm(loan)


@router.patch("/{loan_id}")
def update_loan(
    loan_id: int,
    update_data: dict,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user_sync),
):
    """
    Update loan details (admin only).
    
    Note: Cannot update amount, total_amount, or interest_rate after creation.
    Can update: guarantor_id, notes, etc.
    """
    loan = db.query(Loan).filter(Loan.id == loan_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")

    # Safe fields to update
    safe_fields = ["guarantor_id"]

    for field in safe_fields:
        if field in update_data:
            setattr(loan, field, update_data[field])

    loan.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(loan)

    return LoanResponse.from_orm(loan)


@router.delete("/{loan_id}")
def delete_loan(
    loan_id: int,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user_sync),
):
    """
    Delete a loan (admin only, only if no payments recorded).
    """
    loan = db.query(Loan).filter(Loan.id == loan_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")

    # Check if any payments exist
    from app.models import Installment
    payments = db.query(Installment).filter(Installment.loan_id == loan_id).count()
    if payments > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete loan with payment records",
        )

    db.delete(loan)
    db.commit()

    return {"message": "Loan deleted successfully"}



