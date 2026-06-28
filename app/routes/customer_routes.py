import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, text, func
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from ..database import get_db
from ..models import Customer, Loan, Arrears, LoanStatus
from ..schemas import (
    CustomerCreate,
    CustomerResponse,
    CustomerCheck,
    CustomerCheckRequest,
    CustomerPhotoUpdate,
)
from typing import List
from ..auth import get_current_user
from ..utils.phone import hash_phone, normalize_phone

# For PDF generation
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib import colors
import os

from ..services.pdf_layout import create_canvas, ensure_space, start_body_y, PAGE_MARGIN
from ..services.loan_service import (
    compute_weekly_progress,
    loan_is_overdue_by_schedule,
    sync_overdue_state,
)

router = APIRouter(prefix="/customers", tags=["customers"])

CLOUDINARY_HOST = "res.cloudinary.com"
ALLOWED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _sanitize_image_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if len(url) > 600:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Image URL is too long",
        )
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Image URL must use HTTPS",
        )
    if CLOUDINARY_HOST not in parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only Cloudinary image URLs are allowed",
        )
    if parsed.path:
        lowered = parsed.path.lower()
        if not any(lowered.endswith(ext) for ext in ALLOWED_EXTENSIONS):
            # Cloudinary can omit extensions when using format=auto.
            # Allow such URLs if they contain '/image/upload' path segment.
            if "/image/upload" not in lowered:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Image URL must point to a valid image resource",
                )
    return url


async def _serialize_loans_with_progress(db: AsyncSession, loans: List[Loan]):
    payload = []
    state_changed = False

    for loan in loans:
        changed = await sync_overdue_state(db, loan)
        state_changed = state_changed or changed

        progress = compute_weekly_progress(loan)
        payload.append(
            {
                "id": loan.id,
                "amount": loan.amount,
                "interest_rate": loan.interest_rate,
                "remaining_amount": loan.remaining_amount,
                "total_amount": loan.total_amount,
                "start_date": loan.start_date,
                "due_date": loan.due_date,
                "status": loan.status.value,
                "created_at": loan.created_at,
                "weekly_progress": progress,
                "weekly_due_amount": progress["weekly_due_amount"],
                "weekly_arrears": progress["arrears_amount"],
                "guarantor": {
                    "id": loan.guarantor.id,
                    "name": loan.guarantor.name,
                    "id_number": loan.guarantor.id_number,
                    "phone": loan.guarantor.phone,
                    "location": loan.guarantor.location,
                    "relationship": loan.guarantor.relationship,
                }
                if loan.guarantor
                else None,
            }
        )

    if state_changed:
        await db.commit()

    return payload


@router.get("/")
async def list_customers(
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """List customers with basic info (paginated, with optional search)"""
    base_stmt = select(Customer)
    
    # 🔍 FILTER FIRST if search query provided
    if q:
        q = q.strip()
        base_stmt = base_stmt.where(
            or_(
                Customer.name.ilike(f"%{q}%"),
                Customer.phone.ilike(f"%{q}%"),
                Customer.id_number.ilike(f"%{q}%"),
                Customer.location.ilike(f"%{q}%"),
            )
        )

    # Total count for pagination
    count_stmt = select(func.count()).select_from(Customer)
    if q:
        count_stmt = count_stmt.where(
            or_(
                Customer.name.ilike(f"%{q}%"),
                Customer.phone.ilike(f"%{q}%"),
                Customer.id_number.ilike(f"%{q}%"),
                Customer.location.ilike(f"%{q}%"),
            )
        )
    total_result = await db.execute(count_stmt)
    total_count = total_result.scalar_one()

    # 📄 THEN paginate
    stmt = base_stmt.order_by(Customer.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    customers = result.scalars().all()

    # Determine each customer's current loan status: Active, Overdue, Defaulter, or Clean
    customer_id_numbers = [c.id_number for c in customers]
    status_by_customer = {}
    if customer_id_numbers:
        loans_result = await db.execute(
            select(Loan.customer_id, Loan.status, Loan.is_defaulter).filter(
                Loan.customer_id.in_(customer_id_numbers),
                Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.OVERDUE]),
            )
        )
        for cust_id, loan_status, is_defaulter in loans_result.fetchall():
            if is_defaulter:
                status_by_customer[cust_id] = "Defaulter"
            elif loan_status == LoanStatus.OVERDUE and status_by_customer.get(cust_id) != "Defaulter":
                status_by_customer[cust_id] = "Overdue"
            elif cust_id not in status_by_customer:
                status_by_customer[cust_id] = "Active"

    # Return serialized payload with computed status and pagination metadata
    return {
        "items": [
            {
                "id": c.id,
                "name": c.name,
                "id_number": c.id_number,
                "phone": c.phone,
                "location": c.location,
                "profile_image_url": c.profile_image_url,
                "created_at": c.created_at,
                "status": status_by_customer.get(c.id_number, "Clean"),
                "has_active_loan": status_by_customer.get(c.id_number) == "Active",
            }
            for c in customers
        ],
        "total": total_count,
        "limit": limit,
        "offset": offset,
    }


@router.get("/by-id-number/{id_number}")
async def get_customer_by_id_number(
    id_number: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    # Find the customer
    result = await db.execute(select(Customer).filter(Customer.id_number == id_number))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")

    # 🔹 Filter only active (and overdue) loans with guarantor relationship loaded
    loans_result = await db.execute(
        select(Loan)
        .options(selectinload(Loan.guarantor), selectinload(Loan.installments))
        .filter(
            Loan.customer_id == customer.id_number,  # customer_id stores id_number
            Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.OVERDUE, LoanStatus.ARREARS])
        )
    )
    loans = loans_result.scalars().all()

    # Fallback: some existing records may have stored the numeric customer.id
    if not loans:
        loans_result = await db.execute(
            select(Loan).filter(Loan.customer_id == str(customer.id))
        )
        loans = loans_result.scalars().all()
    loan_payload = await _serialize_loans_with_progress(db, loans)

    # Return the customer and only active loans
    return {
        "id": customer.id,
        "name": customer.name,
        "id_number": customer.id_number,
        "phone": customer.phone,
        "location": customer.location,
        "profile_image_url": customer.profile_image_url,
        "created_at": customer.created_at,
        "loans": loan_payload,
    }


@router.get("/{customer_id}")
async def get_customer_by_id(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Get customer by ID with loans and arrears"""
    result = await db.execute(select(Customer).filter(Customer.id == customer_id))
    customer = result.scalar_one_or_none()

    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found"
        )
    
    # Get customer loans with guarantor relationship loaded
    loans_result = await db.execute(
        select(Loan)
        .options(selectinload(Loan.guarantor), selectinload(Loan.installments))
        .filter(Loan.customer_id == customer.id_number)
    )
    loans = loans_result.scalars().all()

    # Fallback: try numeric customer.id string if no loans found
    if not loans:
        loans_result = await db.execute(
            select(Loan).filter(Loan.customer_id == str(customer.id))
        )
        loans = loans_result.scalars().all()

    loan = next((l for l in loans if l.status.value in ["ACTIVE", "ARREARS", "OVERDUE"]), loans[0] if loans else None)
    loan_payload = await _serialize_loans_with_progress(db, loans)
    
    # Get customer arrears
    arrears_result = await db.execute(
        select(Arrears).filter(Arrears.customer_id == customer.id)
    )
    arrears_list = arrears_result.scalars().all()
    
    # Get recent installments (for dashboard section below arrears)
    installments_query = """
        SELECT i.id, i.amount, i.payment_date, l.id as loan_id
        FROM installments i
        JOIN loans l ON i.loan_id = l.id
        JOIN customers c ON l.customer_id = c.id_number
        WHERE c.id = :cid
        ORDER BY i.payment_date DESC
        LIMIT 10
    """
    inst_result = await db.execute(text(installments_query), {"cid": customer.id})
    inst_rows = inst_result.fetchall()
    
    return {
        "id": customer.id,
        "name": customer.name,
        "id_number": customer.id_number,
        "phone": customer.phone,
        "location": customer.location,
        "profile_image_url": customer.profile_image_url,
        "created_at": customer.created_at,
        "loans": loan_payload,
        "arrears": [
            {
                "id": arrears.id,
                "original_amount": arrears.original_amount,
                "remaining_amount": arrears.remaining_amount,
                "arrears_date": arrears.arrears_date,
                "is_cleared": arrears.is_cleared,
                "created_at": arrears.created_at
            } for arrears in arrears_list
        ],
        "installments": [
            {
                "id": r.id,
                "amount": r.amount,
                "payment_date": r.payment_date,
                "loan_id": r.loan_id
            }
            for r in inst_rows
        ]
    }


@router.post("/check", response_model=CustomerCheck)
async def check_customer_eligibility(
    request: CustomerCheckRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Check if customer exists and whether they have active loans or arrears"""
    # Determine lookup key
    customer = None
    if request.customer_id is not None:
        result = await db.execute(select(Customer).filter(Customer.id == request.customer_id))
        customer = result.scalar_one_or_none()
    elif request.id_number is not None:
        result = await db.execute(select(Customer).filter(Customer.id_number == request.id_number))
        customer = result.scalar_one_or_none()

    # If not found — just return False values (not an error)
    if not customer:
        return {
            "exists": False,
            "has_active_loan": False,
            "has_overdue_loans": False,
            "customer": None
        }

    # Check for active (status == ACTIVE) loans only
    loan_result = await db.execute(
        select(Loan).filter(
            Loan.customer_id == customer.id_number,
            Loan.status == LoanStatus.ACTIVE  # Only ACTIVE
        )
    )
    active_loan = loan_result.scalar_one_or_none()

    # Check for overdue loans either by stored status or via Arrears table
    overdue_result = await db.execute(
        select(Loan).filter(
            Loan.customer_id == customer.id_number,
            Loan.status == LoanStatus.OVERDUE
        )
    )
    has_overdue_loan = overdue_result.scalar_one_or_none() is not None

    if not has_overdue_loan:
        all_loans_result = await db.execute(
            select(Loan).filter(Loan.customer_id == customer.id_number)
        )
        for loan in all_loans_result.scalars().all():
            if loan_is_overdue_by_schedule(loan):
                has_overdue_loan = True
                break

    arrears_result = await db.execute(
        select(Arrears).filter(
            Arrears.customer_id == customer.id,
            Arrears.is_cleared == False
        )
    )
    active_overdue_records = arrears_result.scalar_one_or_none() is not None
    has_overdue_loan = has_overdue_loan or active_overdue_records

    return {
        "exists": True,
        "has_active_loan": active_loan is not None,
        "has_overdue_loans": has_overdue_loan,
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "id_number": customer.id_number,
            "phone": customer.phone,
            "location": customer.location,
            "profile_image_url": customer.profile_image_url,
            "created_at": customer.created_at,
        }
    }


@router.post("/", response_model=CustomerResponse)
async def create_customer(
    customer: CustomerCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Create a new customer"""
    normalized_phone = customer.phone

    existing = await db.execute(
        select(Customer).filter(
            or_(
                Customer.id_number == customer.id_number,
                Customer.phone == normalized_phone,
            )
        )
    )
    existing_customer = existing.scalar_one_or_none()
    if existing_customer:
        field = (
            "id_number" if existing_customer.id_number == customer.id_number else "phone"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Customer with this {field} already exists",
        )

    payload = customer.dict()
    payload["profile_image_url"] = _sanitize_image_url(payload.get("profile_image_url"))
    payload["phone"] = normalized_phone
    payload["phone_hash"] = hash_phone(normalized_phone)

    db_customer = Customer(**payload)
    db.add(db_customer)
    await db.commit()
    await db.refresh(db_customer)
    return db_customer


@router.patch("/{customer_id}/photo", response_model=CustomerResponse)
async def update_customer_photo(
    customer_id: int,
    payload: CustomerPhotoUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update only the customer's profile image URL."""
    sanitized_url = _sanitize_image_url(payload.profile_image_url)
    result = await db.execute(select(Customer).filter(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")

    customer.profile_image_url = sanitized_url
    await db.commit()
    await db.refresh(customer)
    return customer


@router.get("/search", response_model=List[CustomerResponse])
async def search_customers(
    q: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Search customers by name, ID number, or phone"""
    if not q:
        return []
    
    result = await db.execute(
        select(Customer).filter(
            or_(
                Customer.name.ilike(f"%{q}%"),
                Customer.id_number.ilike(f"%{q}%"),
                Customer.phone.ilike(f"%{q}%")
            )
        ).limit(20)
    )
    return result.scalars().all()


# 🆕 Additional endpoints (unchanged from original)
# (Keeping the same delete, report, and statement endpoints)

@router.get("/{customer_id}/installments")
async def get_customer_installments(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Return recent installments for a given customer"""
    query = """
        SELECT i.id, i.amount, i.payment_date, i.recorded_by, i.source, l.id as loan_id
        FROM installments i
        JOIN loans l ON i.loan_id = l.id
        JOIN customers c ON l.customer_id = c.id_number
        WHERE c.id = :cid
        ORDER BY i.payment_date DESC
        LIMIT 10
    """
    result = await db.execute(text(query), {"cid": customer_id})
    rows = result.fetchall()

    return [
        {
            "id": r.id,
            "amount": r.amount,
            "payment_date": r.payment_date,
            "recorded_by": r.recorded_by or "System",
            "source": r.source,
            "loan_id": r.loan_id
        }
        for r in rows
    ]


@router.delete("/{customer_id}")
async def delete_customer(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Delete a customer and all related records"""
    result = await db.execute(select(Customer).filter(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found"
        )
    
    # Check for active loans
    active_loans_result = await db.execute(
        select(Loan).filter(
            Loan.customer_id == customer.id_number,
            Loan.status == LoanStatus.ACTIVE
        )
    )
    active_loans = active_loans_result.scalars().all()
    
    if active_loans:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete customer with active loans"
        )
    
    # Check for overdue loans
    overdue_loans_result = await db.execute(
        select(Loan).filter(
            Loan.customer_id == customer.id_number,
            Loan.status.in_([LoanStatus.OVERDUE, LoanStatus.ARREARS])
        )
    )
    overdue_loans = overdue_loans_result.scalars().all()
    
    if overdue_loans:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete customer with overdue loans"
        )
    
    # Check for active arrears
    active_arrears_result = await db.execute(
        select(Arrears).filter(
            Arrears.customer_id == customer.id,
            Arrears.is_cleared == False
        )
    )
    active_arrears = active_arrears_result.scalars().all()
    
    if active_arrears:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete customer with active arrears"
        )
    
    # Delete all loans and their cascading installments
    all_loans_result = await db.execute(
        select(Loan).filter(Loan.customer_id == customer.id_number)
    )
    all_loans = all_loans_result.scalars().all()
    
    for loan in all_loans:
        await db.delete(loan)
    
    # Delete all arrears
    all_arrears_result = await db.execute(
        select(Arrears).filter(Arrears.customer_id == customer.id)
    )
    all_arrears = all_arrears_result.scalars().all()
    
    for arrears in all_arrears:
        await db.delete(arrears)
    
    # Delete customer
    await db.delete(customer)
    await db.commit()
    
    return {
        "message": "Customer and all related records deleted successfully",
        "customer_id": customer_id
    }



@router.get("/{customer_id}/statement")
async def get_customer_statement(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Full banking-style statement for a customer: every loan ever taken,
    every installment payment, with running balance after each payment.
    """
    result = await db.execute(select(Customer).filter(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")

    loans_result = await db.execute(
        select(Loan)
        .options(selectinload(Loan.installments), selectinload(Loan.guarantor))
        .filter(Loan.customer_id == customer.id_number)
        .order_by(Loan.start_date.asc(), Loan.created_at.asc())
    )
    loans = loans_result.scalars().all()

    loan_statements = []
    lifetime_borrowed = 0.0
    lifetime_paid = 0.0

    for loan in loans:
        installments = sorted(loan.installments or [], key=lambda i: i.payment_date)
        running_balance = float(loan.total_amount or 0)
        ledger = []
        for inst in installments:
            running_balance = max(0.0, running_balance - float(inst.amount or 0))
            ledger.append({
                "installment_id": inst.id,
                "payment_date": inst.payment_date,
                "amount": inst.amount,
                "payment_method": inst.payment_method,
                "reference_number": inst.reference_number,
                "balance_after": round(running_balance, 2),
            })
            lifetime_paid += float(inst.amount or 0)

        lifetime_borrowed += float(loan.amount or 0)

        loan_statements.append({
            "loan_id": loan.id,
            "amount": loan.amount,
            "interest_rate": loan.interest_rate,
            "total_amount": loan.total_amount,
            "remaining_amount": loan.remaining_amount,
            "start_date": loan.start_date,
            "due_date": loan.due_date,
            "completed_at": loan.completed_at,
            "status": loan.status.value,
            "guarantor": ({
                "name": loan.guarantor.name,
                "phone": loan.guarantor.phone,
            } if loan.guarantor else None),
            "installments": ledger,
        })

    return {
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "id_number": customer.id_number,
            "phone": customer.phone,
            "location": customer.location,
            "registered_at": customer.created_at,
        },
        "summary": {
            "total_loans": len(loans),
            "lifetime_borrowed": round(lifetime_borrowed, 2),
            "lifetime_paid": round(lifetime_paid, 2),
        },
        "loans": loan_statements,
    }
