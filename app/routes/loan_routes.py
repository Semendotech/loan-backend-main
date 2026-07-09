"""
CORRECTED Loan Routes
- /active endpoint uses CORRECT filter: days since creation <= 30
- Removed broken due_date filter
- Integrated with LoanService for proper status sync
"""

import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from app.utils.timezone import now_eat
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel, computed_field

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

class GuarantorRequest(BaseModel):
    name: str
    id_number: str
    phone: str
    location: Optional[str] = None
    relationship: Optional[str] = None

class LoanRequest(BaseModel):
    id_number: str
    amount: float
    interest_rate: float = 20.0
    start_date: Optional[str] = None
    guarantor: Optional[GuarantorRequest] = None

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

    @computed_field
    @property
    def days_to_repay(self) -> Optional[int]:
        if not self.completed_at or not self.start_date:
            return None
        return (self.completed_at.date() - self.start_date.date()).days


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

@router.post("")
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
    from datetime import datetime as _dt
    from app.models import Guarantor, Customer
    try:
        customer = db.query(Customer).filter(Customer.id_number == loan_data.id_number).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Block if customer has any loan with remaining balance > 0
        from app.models import Loan as LoanModel
        open_loan = db.query(LoanModel).filter(
            LoanModel.customer_id == customer.id,
            LoanModel.remaining_amount > 0,
        ).first()
        if open_loan:
            raise HTTPException(status_code=400, detail="Customer has an outstanding loan balance. Clear it before issuing a new loan.")

        guarantor_id = None
        if loan_data.guarantor:
            g = loan_data.guarantor
            guarantor = Guarantor(
                name=g.name,
                id_number=g.id_number,
                phone=g.phone,
                location=g.location,
                relationship=g.relationship,
            )
            db.add(guarantor)
            db.flush()
            guarantor_id = guarantor.id

        start_date = None
        if loan_data.start_date:
            try:
                start_date = _dt.strptime(loan_data.start_date, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD.")

        loan = LoanService.create_loan(
            db=db,
            customer_id=customer.id_number,
            amount=loan_data.amount,
            guarantor_id=guarantor_id,
            interest_rate=20.0,
            start_date=start_date,
        )
        # Send SMS notification to customer
        if customer and customer.phone:
            from app.routes.mpesa_routes import send_sms
            loan_message = f"Loan of KSh {loan_data.amount} approved. Due date: {loan.due_date}. Daily instalment: KSh {loan.total_amount / 30:.2f}. Call 0718016498 for inquiries."
            try:
                asyncio.run(send_sms(customer.phone, loan_message))
            except Exception as sms_err:
                print(f">>> LOAN SMS FAILED: {sms_err}", flush=True)

        return LoanResponse.from_orm(loan)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/active")
def get_active_loans(
    limit: int = Query(50, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    q: str = Query("", alias="q"),
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
        loans, total = LoanService.get_active_loans(db, limit=limit, offset=offset, search=q)
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


@router.get("/payable")
def get_payable_loans(
    limit: int = Query(50, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    q: str = Query("", alias="q"),
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user_sync),
):
    """
    Get loans with an outstanding balance for the Pay Installments page.
    Includes ACTIVE, OVERDUE, and ARREARS statuses.
    """
    import traceback
    try:
        loans, total = LoanService.get_payable_loans(db, limit=limit, offset=offset, search=q)
    except Exception as e:
        print('PAYABLE ERROR:', traceback.format_exc())
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

    loan.updated_at = now_eat()
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















# ─── Disbursed loans list ───────────────────────────────────────────────
@router.get("/disbursed", response_model=LoanListResponse)
def get_disbursed_loans(
    start_date: str = None,
    end_date: str = None,
    q: str = None,
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Loans disbursed (start_date) within the given date range.
    """
    from datetime import datetime as _dt, date as _date
    from sqlalchemy.orm import selectinload

    today = _date.today()
    try:
        d_start = _dt.strptime(start_date, "%Y-%m-%d").date() if start_date else today
        d_end   = _dt.strptime(end_date,   "%Y-%m-%d").date() if end_date   else today
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    query = (
        db.query(Loan)
        .options(selectinload(Loan.customer))
        .filter(Loan.start_date >= d_start, Loan.start_date <= d_end)
    )
    if q and q.strip():
        search = f"%{q.strip()}%"
        query = query.join(Loan.customer).filter(
            (Customer.name.ilike(search)) |
            (Customer.id_number.ilike(search)) |
            (Customer.phone.ilike(search))
        )
    loans = query.order_by(Loan.start_date.desc()).offset(skip).limit(limit).all()
    total = query.count()

    items = []
    for loan in loans:
        c = loan.customer
        items.append(LoanItem(
            id=loan.id,
            amount=loan.amount,
            total_amount=loan.total_amount,
            remaining_amount=loan.remaining_amount,
            status=loan.status.value if loan.status else "ACTIVE",
            start_date=str(loan.start_date) if loan.start_date else None,
            due_date=str(loan.due_date) if loan.due_date else None,
            daily_instalment=loan.daily_instalment if hasattr(loan, "daily_instalment") else None,
            days_to_repay=loan.days_to_repay if hasattr(loan, "days_to_repay") else None,
            completed_at=loan.completed_at.isoformat() if loan.completed_at else None,
            customer=CustomerSummary(
                name=c.name if c else None,
                id_number=c.id_number if c else "-",
                phone=c.phone if c else None,
            ) if c else None,
        ))
    return LoanListResponse(items=items, total=total)
