"""
CORRECTED Payment Routes
- Integrated defaulter checking when payments recorded
- Proper balance sync between Loan and Arrears
- Status updates after payment
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from typing import Optional

from app.database import get_sync_db
from app.models import Loan, Installment, Arrears, LoanStatus
from app.services.loan_service import LoanService
from app.auth import get_current_user

router = APIRouter(prefix="/payments", tags=["payments"])


# ============ SCHEMAS ============

class PaymentRequest(BaseModel):
    loan_id: int
    amount: float
    payment_method: str = "CASH"
    reference_number: Optional[str] = None

    class Config:
        from_attributes = True


class PaymentResponse(BaseModel):
    id: int
    loan_id: int
    amount: float
    payment_date: datetime
    payment_method: Optional[str]
    reference_number: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class InstallmentListResponse(BaseModel):
    items: list[PaymentResponse]
    total: int
    limit: int
    offset: int

    class Config:
        from_attributes = True


# ============ ENDPOINTS ============

@router.post("/record", response_model=PaymentResponse)
def record_payment(
    payment: PaymentRequest,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Record a payment against a loan.
    
    Process Flow:
    1. Validate loan exists
    2. Record installment
    3. Reduce Loan.remaining_amount
    4. Sync Arrears.remaining_amount
    5. Check defaulter status (if ACTIVE period)
    6. Update loan status (might become COMPLETED or OVERDUE)
    
    Business Logic Applied:
    - Payment amount is recorded exactly as provided
    - Loan.remaining_amount -= amount
    - If remaining <= 0, status becomes COMPLETED
    - If in ACTIVE period, check 5-day defaulter rule
    """
    loan = db.query(Loan).filter(Loan.id == payment.loan_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")

    try:
        # Record payment using LoanService (handles all syncing)
        recorded_by_name = (getattr(current_user, "first_name", None) or getattr(current_user, "username", None) or "Unknown")
        installment = LoanService.record_payment(
            db=db,
            loan_id=payment.loan_id,
            amount=payment.amount,
            payment_method=payment.payment_method,
            reference=payment.reference_number,
            recorded_by=recorded_by_name,
        )

        from datetime import datetime as _dt
        from sqlalchemy.orm import selectinload
        loan_after = db.query(Loan).options(selectinload(Loan.customer)).filter(Loan.id == payment.loan_id).first()
        customer_name = loan_after.customer.name if loan_after and loan_after.customer else "Unknown"
        balance_after = float(loan_after.remaining_amount) if loan_after else 0
        now = _dt.utcnow().isoformat(timespec="seconds")
        ref = payment.reference_number or "N/A"
        print("", flush=True)
        print("========== PAYMENT RECEIVED ==========", flush=True)
        print("Time         : " + now + " UTC", flush=True)
        print("Customer     : " + customer_name, flush=True)
        print("Amount       : KES " + str(round(payment.amount, 2)), flush=True)
        print("Ref Number   : " + ref, flush=True)
        print("Loan Balance : KES " + str(round(balance_after, 2)), flush=True)
        print("Recorded By  : " + recorded_by_name, flush=True)
        print("======================================", flush=True)
        print("", flush=True)
        return PaymentResponse.from_orm(installment)

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/loan/{loan_id}", response_model=InstallmentListResponse)
def get_loan_payments(
    loan_id: int,
    limit: int = Query(50, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """Get all payments (installments) for a loan"""
    loan = db.query(Loan).filter(Loan.id == loan_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")

    query = db.query(Installment).filter(Installment.loan_id == loan_id).order_by(
        Installment.payment_date.desc()
    )

    total = query.count()
    installments = query.limit(limit).offset(offset).all()

    return InstallmentListResponse(
        items=[PaymentResponse.from_orm(i) for i in installments],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/summary/{loan_id}")
def get_payment_summary(
    loan_id: int,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Get payment summary for a loan.
    
    Returns:
    {
        "total_amount": Expected total to pay,
        "total_paid": Sum of all payments,
        "remaining_amount": Still owed,
        "daily_instalment": Expected daily payment,
        "payments_count": Number of payment records,
        "status": Current loan status,
        "is_defaulter": Whether loan is flagged as defaulter,
        "days_since_start": Days elapsed,
        "is_active_period": Still within 30 days,
    }
    """
    loan = db.query(Loan).filter(Loan.id == loan_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")

    # Sync status first
    LoanService.sync_loan_status(db, loan)

    # Get total paid
    from sqlalchemy import func
    total_paid = db.query(func.sum(Installment.amount)).filter(
        Installment.loan_id == loan_id
    ).scalar() or 0

    payments_count = db.query(func.count(Installment.id)).filter(
        Installment.loan_id == loan_id
    ).scalar()

    return {
        "total_amount": loan.total_amount,
        "total_paid": total_paid,
        "remaining_amount": loan.remaining_amount,
        "daily_instalment": loan.daily_instalment,
        "payments_count": payments_count,
        "status": loan.status.value,
        "is_defaulter": loan.is_defaulter,
        "days_since_start": loan.days_since_start,
        "is_active_period": loan.is_active_period,
    }


@router.put("/installment/{installment_id}")
def update_installment(
    installment_id: int,
    update_data: dict,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Update an installment (admin only, for corrections).
    
    WARNING: Changing amount will affect loan balance!
    """
    installment = db.query(Installment).filter(
        Installment.id == installment_id
    ).first()
    if not installment:
        raise HTTPException(status_code=404, detail="Installment not found")

    loan = installment.loan

    # If amount changed, adjust loan balance
    if "amount" in update_data:
        old_amount = installment.amount
        new_amount = update_data["amount"]
        diff = new_amount - old_amount

        loan.remaining_amount -= diff
        if loan.remaining_amount < 0:
            loan.remaining_amount = 0

        installment.amount = new_amount

    # Update other fields
    safe_fields = ["payment_method", "reference_number"]
    for field in safe_fields:
        if field in update_data:
            setattr(installment, field, update_data[field])

    loan.updated_at = datetime.utcnow()
    db.commit()

    # Sync balances
    LoanService.sync_arrears_balance(db, loan)

    # Check defaulter status again
    if loan.is_active_period:
        LoanService.check_defaulter_status(db, loan.id)

    db.refresh(installment)
        from datetime import datetime as _dt
        from sqlalchemy.orm import selectinload
        loan_after = db.query(Loan).options(selectinload(Loan.customer)).filter(Loan.id == payment.loan_id).first()
        customer_name = loan_after.customer.name if loan_after and loan_after.customer else "Unknown"
        balance_after = float(loan_after.remaining_amount) if loan_after else 0
        now = _dt.utcnow().isoformat(timespec="seconds")
        ref = payment.reference_number or "N/A"
        print("", flush=True)
        print("========== PAYMENT RECEIVED ==========", flush=True)
        print("Time         : " + now + " UTC", flush=True)
        print("Customer     : " + customer_name, flush=True)
        print("Amount       : KES " + str(round(payment.amount, 2)), flush=True)
        print("Ref Number   : " + ref, flush=True)
        print("Loan Balance : KES " + str(round(balance_after, 2)), flush=True)
        print("Recorded By  : " + recorded_by_name, flush=True)
        print("======================================", flush=True)
        print("", flush=True)
        return PaymentResponse.from_orm(installment)


@router.delete("/installment/{installment_id}")
def delete_installment(
    installment_id: int,
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Delete an installment (admin only, for corrections).
    
    WARNING: Will restore amount to loan remaining_amount!
    """
    installment = db.query(Installment).filter(
        Installment.id == installment_id
    ).first()
    if not installment:
        raise HTTPException(status_code=404, detail="Installment not found")

    loan = installment.loan
    loan.remaining_amount += installment.amount  # Restore the amount

    db.delete(installment)
    loan.updated_at = datetime.utcnow()
    db.commit()

    # Sync balances
    LoanService.sync_arrears_balance(db, loan)

    # Check defaulter status again
    if loan.is_active_period:
        LoanService.check_defaulter_status(db, loan.id)

    return {"message": "Installment deleted successfully"}



@router.get("/all")
def get_all_payments(
    limit: int = Query(50, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    start_date: str = Query(None),
    end_date: str = Query(None),
    q: str = Query(None),
    db: Session = Depends(get_sync_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Get all payments across all loans, with optional date range and search.
    """
    from datetime import datetime as _dt
    from sqlalchemy.orm import selectinload
    from app.models import Customer

    query = (
        db.query(Installment)
        .options(selectinload(Installment.loan).selectinload(Loan.customer))
    )

    if start_date:
        try:
            dt_start = _dt.strptime(start_date, "%Y-%m-%d")
            query = query.filter(Installment.payment_date >= dt_start)
        except ValueError:
            pass

    if end_date:
        try:
            dt_end = _dt.combine(_dt.strptime(end_date, "%Y-%m-%d").date(), _dt.max.time())
            query = query.filter(Installment.payment_date <= dt_end)
        except ValueError:
            pass

    if q and q.strip():
        search = f"%{q.strip()}%"
        query = query.join(Installment.loan).join(Loan.customer).filter(
            (Customer.name.ilike(search)) |
            (Customer.id_number.ilike(search)) |
            (Customer.phone.ilike(search))
        )

    total = query.count()
    installments = query.order_by(Installment.payment_date.desc()).limit(limit).offset(offset).all()

    items = []
    for inst in installments:
        loan = inst.loan
        customer = loan.customer if loan else None
        items.append({
            "id": inst.id,
            "loan_id": inst.loan_id,
            "customer_name": customer.name if customer else "-",
            "customer_id_number": customer.id_number if customer else "-",
            "customer_phone": customer.phone if customer else "-",
            "amount": float(inst.amount or 0),
            "balance_after": float(inst.balance_after) if inst.balance_after is not None else None,
            "payment_date": inst.payment_date.isoformat() if inst.payment_date else None,
            "payment_method": inst.payment_method or "CASH",
            "recorded_by": inst.recorded_by or "System",
            "reference_number": inst.reference_number,
        })

    return {"items": items, "total": total, "limit": limit, "offset": offset}


