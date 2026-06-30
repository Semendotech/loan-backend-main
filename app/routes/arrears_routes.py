"""
CORRECTED Arrears Routes
- ARREARS = Unpaid balances on OVERDUE loans (day 31+)
- Track via Arrears table with is_cleared flag
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from app.utils.timezone import now_eat
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from typing import Optional

from app.database import get_sync_db
from app.models import Arrears, Loan, LoanStatus, Installment, Customer
from app.services.loan_service import LoanService
from app.auth import get_current_user

router = APIRouter(prefix="/arrears", tags=["arrears"])


# ============ SCHEMAS ============

class ArrearsResponse(BaseModel):
    id: int
    loan_id: int
    customer_id: int
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_id_number: Optional[str] = None
    original_amount: float
    remaining_amount: float
    is_cleared: bool
    arrears_date: datetime
    cleared_date: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class ArrearsListResponse(BaseModel):
    items: list[ArrearsResponse]
    total: int
    limit: int
    offset: int

    class Config:
        from_attributes = True


# ============ ENDPOINTS ============

@router.get("", response_model=ArrearsListResponse)
def get_arrears(
    only_active: bool = Query(True),
    q: Optional[str] = Query(None, description="Search by customer name, phone, or ID number"),
    limit: int = Query(50, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Get arrears (unpaid balances on overdue loans).
    
    Filters:
    - only_active=True (default): is_cleared == false (actively overdue)
    - only_active=False: all arrears records (including cleared)
    
    Business Logic:
    - Arrears = Loans that have exceeded 30-day window (day 31+)
    - is_cleared = false means still owed
    - is_cleared = true means overdue loan was fully paid
    """
    # Note: full sync is handled by the scheduled /admin/sync job, not on every page load
    from sqlalchemy.orm import selectinload
    from sqlalchemy import or_

    query = db.query(Arrears).options(selectinload(Arrears.customer))

    if only_active:
        query = query.filter(Arrears.is_cleared == False)

    if q:
        search = f"%{q.strip()}%"
        query = query.join(Customer, Arrears.customer_id == Customer.id).filter(
            or_(
                Customer.name.ilike(search),
                Customer.phone.ilike(search),
                Customer.id_number.ilike(search),
            )
        )

    total = query.count()
    arrears_records = query.order_by(Arrears.arrears_date.desc()).limit(limit).offset(offset).all()

    items = []
    for a in arrears_records:
        resp = ArrearsResponse.model_validate(a, from_attributes=True)
        if a.customer:
            resp.customer_name = a.customer.name
            resp.customer_phone = a.customer.phone
            resp.customer_id_number = a.customer.id_number
        items.append(resp)

    return ArrearsListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{arrears_id}", response_model=ArrearsResponse)
def get_arrears_detail(
    arrears_id: int,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """Get details of a specific arrears record"""
    arrears = db.query(Arrears).filter(Arrears.id == arrears_id).first()
    if not arrears:
        raise HTTPException(status_code=404, detail="Arrears record not found")

    return ArrearsResponse.from_orm(arrears)


@router.post("/{arrears_id}/payment")
def record_arrears_payment(
    arrears_id: int,
    payment_data: dict,  # {"amount": float, "payment_method": str}
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Record a payment against arrears (overdue loan).
    
    Process:
    1. Get the associated loan
    2. Record payment via LoanService
    3. Check if arrears should be cleared (remaining = 0)
    """
    arrears = db.query(Arrears).filter(Arrears.id == arrears_id).first()
    if not arrears:
        raise HTTPException(status_code=404, detail="Arrears record not found")

    amount = payment_data.get("amount", 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than 0")

    try:
        # Record payment via LoanService
        LoanService.record_payment(
            db=db,
            loan_id=arrears.loan_id,
            amount=amount,
            payment_method=payment_data.get("payment_method", "CASH"),
            reference=payment_data.get("reference_number"),
        )

        # Refresh arrears
        db.refresh(arrears)

        return ArrearsResponse.from_orm(arrears)

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{arrears_id}/clear")
def clear_arrears(
    arrears_id: int,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Manually mark arrears as cleared (admin only).
    
    Only use if loan has been fully paid and system needs correction.
    """
    arrears = db.query(Arrears).filter(Arrears.id == arrears_id).first()
    if not arrears:
        raise HTTPException(status_code=404, detail="Arrears record not found")

    if arrears.is_cleared:
        raise HTTPException(status_code=400, detail="Arrears already cleared")

    arrears.is_cleared = True
    arrears.cleared_date = now_eat()
    arrears.remaining_amount = 0

    # Update associated loan
    loan = arrears.loan
    loan.status = LoanStatus.COMPLETED
    loan.remaining_amount = 0
    loan.completed_at = now_eat()

    db.commit()
    db.refresh(arrears)

    return ArrearsResponse.from_orm(arrears)


@router.get("/loan/{loan_id}")
def get_loan_arrears(
    loan_id: int,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """Get arrears record for a specific loan (if it exists)"""
    arrears = db.query(Arrears).filter(Arrears.loan_id == loan_id).first()

    if not arrears:
        return {"message": "No arrears record for this loan"}

    return ArrearsResponse.from_orm(arrears)


@router.get("/customer/{customer_id}")
def get_customer_arrears(
    customer_id: str,
    limit: int = Query(50, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """Get all arrears records for a customer"""
    query = db.query(Arrears).filter(Arrears.customer_id == customer_id)

    total = query.count()
    arrears_records = query.order_by(Arrears.arrears_date.desc()).limit(limit).offset(offset).all()

    return {
        "items": [ArrearsResponse.from_orm(a) for a in arrears_records],
        "total": total,
        "limit": limit,
        "offset": offset,
    }



