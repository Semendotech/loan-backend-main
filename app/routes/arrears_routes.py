from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from datetime import datetime
from pydantic import BaseModel
from typing import Optional
from ..database import get_db
from ..models import Arrears, Customer, Loan, LoanStatus, Installment
from ..auth import get_current_user

router = APIRouter(prefix="/arrears", tags=["arrears"])


@router.get("/")
async def list_arrears(
    only_active: bool = True,
    limit: int = 1000,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    q = select(Arrears).options(selectinload(Arrears.customer)).order_by(Arrears.created_at.desc())
    if only_active:
        q = q.filter(Arrears.is_cleared == False)
    q = q.limit(limit).offset(offset)
    res = await db.execute(q)
    arrears_list = res.scalars().all()
    return [
        {
            "id": a.id,
            "customer_id": a.customer_id,
            "customer_name": a.customer.name if a.customer else None,
            "loan_id": a.loan_id,
            "original_amount": a.original_amount,
            "remaining_amount": a.remaining_amount,
            "arrears_date": a.arrears_date,
            "is_cleared": a.is_cleared,
            "created_at": a.created_at,
        } for a in arrears_list
    ]


@router.get("/{arrears_id}")
async def get_arrears(arrears_id: int,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    res = await db.execute(select(Arrears).filter(Arrears.id == arrears_id))
    arrears = res.scalar_one_or_none()
    if not arrears:
        raise HTTPException(status_code=404, detail="Arrears not found")
    return {
        "id": arrears.id,
        "customer_id": arrears.customer_id,
        "loan_id": arrears.loan_id,
        "original_amount": arrears.original_amount,
        "remaining_amount": arrears.remaining_amount,
        "arrears_date": arrears.arrears_date,
        "is_cleared": arrears.is_cleared,
        "created_at": arrears.created_at,
    }


class ArrearsPayment(BaseModel):
    amount: float


@router.post("/{arrears_id}/installments")
async def pay_arrears(arrears_id: int, body: ArrearsPayment,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    res = await db.execute(select(Arrears).filter(Arrears.id == arrears_id))
    arrears = res.scalar_one_or_none()
    if not arrears:
        raise HTTPException(status_code=404, detail="Arrears not found")
    if arrears.is_cleared:
        raise HTTPException(status_code=400, detail="Arrears already cleared")
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    payment_amount = float(body.amount)
    arrears.remaining_amount = max(0.0, float(arrears.remaining_amount) - payment_amount)

    # Sync loan remaining amount and status
    loan_res = await db.execute(select(Loan).filter(Loan.id == arrears.loan_id))
    loan = loan_res.scalar_one_or_none()
    if loan:
        loan.remaining_amount = arrears.remaining_amount
        if arrears.remaining_amount == 0:
            arrears.is_cleared = True
            arrears.cleared_date = datetime.utcnow()
            loan.status = LoanStatus.COMPLETED
            loan.completed_at = datetime.utcnow()
        else:
            # Still owing; overdue loans remain in OVERDUE status
            loan.status = LoanStatus.OVERDUE
        db.add(loan)

    # Create an Installment record to track this payment for weekly/monthly reporting
    installment = Installment(
        loan_id=arrears.loan_id,
        amount=payment_amount,
        payment_date=datetime.utcnow()
    )
    db.add(installment)

    db.add(arrears)
    await db.commit()
    await db.refresh(arrears)
    await db.refresh(installment)
    return {
        "message": "Arrears payment recorded",
        "remaining_amount": arrears.remaining_amount,
        "is_cleared": arrears.is_cleared,
        "installment_id": installment.id
    }


@router.post("/{arrears_id}/clear")
async def clear_arrears(arrears_id: int,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    res = await db.execute(select(Arrears).filter(Arrears.id == arrears_id))
    arrears = res.scalar_one_or_none()
    if not arrears:
        raise HTTPException(status_code=404, detail="Arrears not found")
    
    # Get the amount being cleared (remaining amount before clearing)
    cleared_amount = float(arrears.remaining_amount)
    
    arrears.remaining_amount = 0.0
    arrears.is_cleared = True
    arrears.cleared_date = datetime.utcnow()
    db.add(arrears)
    
    # also complete linked loan and zero remaining
    loan_res = await db.execute(select(Loan).filter(Loan.id == arrears.loan_id))
    loan = loan_res.scalar_one_or_none()
    if loan and loan.status != LoanStatus.COMPLETED:
        loan.remaining_amount = 0.0
        loan.status = LoanStatus.COMPLETED
        loan.completed_at = datetime.utcnow()
        db.add(loan)
    
    # Create an Installment record to track this payment for weekly/monthly reporting
    # Only create if there's an amount being cleared
    if cleared_amount > 0:
        installment = Installment(
            loan_id=arrears.loan_id,
            amount=cleared_amount,
            payment_date=datetime.utcnow()
        )
        db.add(installment)
    
    await db.commit()
    await db.refresh(arrears)
    return {"message": "Arrears cleared"}



















