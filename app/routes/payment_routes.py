from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, and_
from datetime import datetime
from pydantic import BaseModel
from typing import Optional
from ..database import get_db
from ..models import Loan, Customer, Installment, LoanStatus
from ..auth import get_current_user
from ..services.loan_service import compute_weekly_progress, sync_overdue_state

router = APIRouter(prefix="/payments", tags=["payments"])


class PaymentCreate(BaseModel):
    id_number: str
    amount: float


class InstallmentUpdate(BaseModel):
    amount: float


@router.post("/")
async def record_payment(
    payment: PaymentCreate,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Record a payment/installment for a customer's loan"""
    
    # Find customer by ID number
    customer_result = await db.execute(
        select(Customer).filter(Customer.id_number == payment.id_number)
    )
    customer = customer_result.scalar_one_or_none()
    
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found"
        )
    
    # Find ACTIVE loan only (overdue loans must be paid through the overdue page)
    loan_result = await db.execute(
        select(Loan).filter(
            and_(
                Loan.customer_id == payment.id_number,
                Loan.status == LoanStatus.ACTIVE
            )
        ).order_by(Loan.id.desc())
    )
    loan = loan_result.scalar_one_or_none()
    
    if not loan:
        # Check if they have overdue/arrears loans
        overdue_check = await db.execute(
            select(Loan).filter(
                and_(
                    Loan.customer_id == payment.id_number,
                    Loan.status.in_([LoanStatus.OVERDUE, LoanStatus.ARREARS])
                )
            )
        )
        has_overdue = overdue_check.scalar_one_or_none() is not None
        
        if has_overdue:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This loan is overdue. Please pay through the Overdue page."
            )
        
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active loan found for this customer"
        )
    
    overdue_state_changed = await sync_overdue_state(db, loan)
    if loan.status != LoanStatus.ACTIVE:
        if overdue_state_changed:
            await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This loan is overdue. Please pay through the Overdue page."
        )

    weekly_before = compute_weekly_progress(loan)

    # Create installment record
    installment = Installment(
        loan_id=loan.id,
        amount=payment.amount,
        payment_date=datetime.utcnow(),
        recorded_by=(current_user.first_name or current_user.username or "User"),
        source="manual",
    )
    
    db.add(installment)
    
    # Update remaining amount on the loan
    current_remaining = loan.remaining_amount if loan.remaining_amount is not None else loan.total_amount
    if payment.amount > current_remaining:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment amount cannot exceed remaining balance",
        )
    new_remaining = max(0.0, current_remaining - payment.amount)
    loan.remaining_amount = new_remaining

    # Determine if fully paid
    if new_remaining <= 0:
        loan.status = LoanStatus.COMPLETED
        loan.completed_at = datetime.utcnow()
    weekly_after = compute_weekly_progress(loan)

    await sync_overdue_state(db, loan)
    
    await db.commit()
    await db.refresh(installment)
    await db.refresh(loan)
    
    return {
        "message": "Payment recorded successfully",
        "installment_id": installment.id,
        "remaining_balance": loan.remaining_amount,
        "loan_status": loan.status.value,
        "weekly_breakdown": {
            "before": weekly_before,
            "after": weekly_after,
        },
    }


@router.put("/installments/{installment_id}")
async def update_installment_amount(
    installment_id: int,
    body: InstallmentUpdate,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the amount of a specific installment and resync the loan balance/overdue state."""
    if body.amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Amount must be positive",
        )

    inst_result = await db.execute(
        select(Installment).filter(Installment.id == installment_id)
    )
    installment = inst_result.scalar_one_or_none()
    if not installment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Installment not found",
        )

    loan_result = await db.execute(
        select(Loan).filter(Loan.id == installment.loan_id)
    )
    loan = loan_result.scalar_one_or_none()
    if not loan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Linked loan not found",
        )

    # Update installment amount
    installment.amount = body.amount

    # Recompute remaining amount from total installments
    total_paid_res = await db.execute(
        select(func.coalesce(func.sum(Installment.amount), 0.0)).filter(
            Installment.loan_id == loan.id
        )
    )
    total_paid = float(total_paid_res.scalar() or 0.0)
    new_remaining = max(0.0, float(loan.total_amount) - total_paid)
    loan.remaining_amount = new_remaining

    # Adjust status based on remaining and due date.
    # This can "re-open" a completed loan if a payment is edited down.
    today = datetime.utcnow().date()
    if new_remaining <= 0:
        # Fully paid -> completed
        loan.status = LoanStatus.COMPLETED
        loan.completed_at = datetime.utcnow()
    else:
        # Not fully paid anymore -> clear completion timestamp
        loan.completed_at = None
        # If past due date, treat as overdue; otherwise active
        if loan.due_date and loan.due_date < today:
            loan.status = LoanStatus.OVERDUE
        else:
            loan.status = LoanStatus.ACTIVE

    await sync_overdue_state(db, loan)

    await db.commit()
    await db.refresh(installment)
    await db.refresh(loan)

    return {
        "message": "Installment updated successfully",
        "installment_id": installment.id,
        "new_amount": installment.amount,
        "remaining_balance": loan.remaining_amount,
        "loan_status": loan.status.value,
    }


@router.delete("/installments/{installment_id}")
async def delete_installment(
    installment_id: int,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a specific installment and resync the loan balance/overdue state.
    This effectively reverses the impact of that installment on the loan.
    """
    inst_result = await db.execute(
        select(Installment).filter(Installment.id == installment_id)
    )
    installment = inst_result.scalar_one_or_none()
    if not installment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Installment not found",
        )

    loan_result = await db.execute(select(Loan).filter(Loan.id == installment.loan_id))
    loan = loan_result.scalar_one_or_none()
    if not loan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Linked loan not found",
        )

    # Remove the installment
    await db.delete(installment)

    # Recompute remaining from remaining installments
    total_paid_res = await db.execute(
        select(func.coalesce(func.sum(Installment.amount), 0.0)).filter(
            Installment.loan_id == loan.id
        )
    )
    total_paid = float(total_paid_res.scalar() or 0.0)
    new_remaining = max(0.0, float(loan.total_amount) - total_paid)
    loan.remaining_amount = new_remaining

    today = datetime.utcnow().date()
    if new_remaining <= 0:
        loan.status = LoanStatus.COMPLETED
        loan.completed_at = datetime.utcnow()
    else:
        loan.completed_at = None
        if loan.due_date and loan.due_date < today:
            loan.status = LoanStatus.OVERDUE
        else:
            loan.status = LoanStatus.ACTIVE

    await sync_overdue_state(db, loan)

    await db.commit()
    await db.refresh(loan)

    return {
        "message": "Installment deleted successfully",
        "remaining_balance": loan.remaining_amount,
        "loan_status": loan.status.value,
    }

