from fastapi import APIRouter, Depends, HTTPException, status, Query
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
from ..services.loan_service import reconcile_stale_arrears

router = APIRouter(prefix="/arrears", tags=["arrears"])


@router.get("/")
async def list_arrears(
    only_active: bool = True,
    limit: int = Query(100, le=10000),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """List arrears with proper pagination showing all 184 loans."""
    if await reconcile_stale_arrears(db):
        await db.commit()

    q = select(Arrears).options(selectinload(Arrears.customer))
    
    if only_active:
        q = q.filter(Arrears.is_cleared == False)
    
    # Get total count BEFORE pagination
    count_result = await db.execute(
        select(func.count(Arrears.id)).filter(
            Arrears.is_cleared == False if only_active else True
        )
    )
    total_count = count_result.scalar() or 0

    # Apply pagination
    q = q.order_by(Arrears.created_at.desc()).limit(limit).offset(offset)
    
    res = await db.execute(q)
    arrears_list = res.scalars().all()
    
    items = [
        {
            "id": a.id,
            "customer_id": a.customer_id,
            "customer_name": a.customer.name if a.customer else None,
            "customer_phone": a.customer.phone if a.customer else None,
            "loan_id": a.loan_id,
            "original_amount": a.original_amount,
            "remaining_amount": a.remaining_amount,
            "arrears_date": a.arrears_date,
            "is_cleared": a.is_cleared,
            "created_at": a.created_at,
        } for a in arrears_list
    ]
    
    return {
        "items": items,
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "count": len(items),
        "has_more": offset + limit < total_count,
        "page": (offset // limit) + 1 if limit > 0 else 1,
        "total_pages": (total_count + limit - 1) // limit if limit > 0 else 1,
    }


@router.get("/{arrears_id}")
async def get_arrears(arrears_id: int,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Get details of a specific arrears record."""
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
    """Record a payment against arrears."""
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

    loan_res = await db.execute(select(Loan).filter(Loan.id == arrears.loan_id))
    loan = loan_res.scalar_one_or_none()
    if loan:
        loan.remaining_amount = arrears.remaining_amount
        if arrears.remaining_amount <= 0:
            arrears.remaining_amount = 0.0
            arrears.is_cleared = True
            arrears.cleared_date = datetime.utcnow()
            loan.status = LoanStatus.COMPLETED
            loan.completed_at = datetime.utcnow()
        else:
            loan.status = LoanStatus.OVERDUE
        db.add(loan)

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
    """Manually clear arrears."""
    res = await db.execute(select(Arrears).filter(Arrears.id == arrears_id))
    arrears = res.scalar_one_or_none()
    if not arrears:
        raise HTTPException(status_code=404, detail="Arrears not found")
    
    cleared_amount = float(arrears.remaining_amount)
    
    arrears.remaining_amount = 0.0
    arrears.is_cleared = True
    arrears.cleared_date = datetime.utcnow()
    db.add(arrears)
    
    loan_res = await db.execute(select(Loan).filter(Loan.id == arrears.loan_id))
    loan = loan_res.scalar_one_or_none()
    if loan and loan.status != LoanStatus.COMPLETED:
        loan.remaining_amount = 0.0
        loan.status = LoanStatus.COMPLETED
        loan.completed_at = datetime.utcnow()
        db.add(loan)
    
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
