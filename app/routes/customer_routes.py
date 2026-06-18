import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, text
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
from reportlab.pdfgen import canvas
import os

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
    
    # 📄 THEN paginate
    stmt = base_stmt.order_by(Customer.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    customers = result.scalars().all()

    # Determine which customers currently have active loans
    customer_id_numbers = [c.id_number for c in customers]
    if customer_id_numbers:
        active_result = await db.execute(
          select(Loan.customer_id).filter(
              Loan.customer_id.in_(customer_id_numbers),
              Loan.status == LoanStatus.ACTIVE
          )
        )
        active_customer_ids = {row[0] for row in active_result.fetchall()}
    else:
        active_customer_ids = set()

    # Return serialized payload with has_active_loan flag
    return [
        {
            "id": c.id,
            "name": c.name,
            "id_number": c.id_number,
            "phone": c.phone,
            "location": c.location,
            "profile_image_url": c.profile_image_url,
            "created_at": c.created_at,
            "has_active_loan": c.id_number in active_customer_ids,
        }
        for c in customers
    ]


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
        .options(selectinload(Loan.guarantor))
        .filter(
            Loan.customer_id == customer.id_number,  # customer_id stores id_number
            Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.OVERDUE, LoanStatus.ARREARS])
        )
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
        .options(selectinload(Loan.guarantor))
        .filter(Loan.customer_id == customer.id_number)
    )
    loans = loans_result.scalars().all()
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

    # Check for active (within-month) loans
    loan_result = await db.execute(
        select(Loan).filter(
            Loan.customer_id == customer.id_number,
            Loan.status == LoanStatus.ACTIVE
        )
    )
    active_loan = loan_result.scalar_one_or_none()

    # Check for overdue loans either by stored status or by schedule
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
    try:
        normalized_phone = normalize_phone(customer.phone)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

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


# 🆕 -----------------------------
# New endpoints added below
# -----------------------------

@router.get("/{customer_id}/installments")
async def get_customer_installments(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Return recent installments for a given customer"""
    query = """
        SELECT i.id, i.amount, i.payment_date, l.id as loan_id
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
    """Delete a customer and all related records.
    Cannot delete if customer has active or overdue loans.
    """
    # Find the customer
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
            detail="Cannot delete customer with active loans. Please complete or cancel all active loans first."
        )
    
    # Check for overdue/arrears loans
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
            detail="Cannot delete customer with overdue loans. Please clear all overdue balances first."
        )
    
    # Check for active arrears records
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
            detail="Cannot delete customer with active arrears. Please clear all arrears first."
        )
    
    # All checks passed - safe to delete
    # Since Loan.customer_id references customers.id_number (not customers.id),
    # we need to manually delete loans first to ensure proper cascading
    # The ORM cascade will handle installments when we delete loans
    
    # Get all loans for this customer
    all_loans_result = await db.execute(
        select(Loan).filter(Loan.customer_id == customer.id_number)
    )
    all_loans = all_loans_result.scalars().all()
    
    # Delete all loans (this will cascade to installments via ORM relationship)
    for loan in all_loans:
        await db.delete(loan)
    
    # Delete all arrears records (they reference customer.id)
    all_arrears_result = await db.execute(
        select(Arrears).filter(Arrears.customer_id == customer.id)
    )
    all_arrears = all_arrears_result.scalars().all()
    
    for arrears in all_arrears:
        await db.delete(arrears)
    
    # Finally, delete the customer
    await db.delete(customer)
    await db.commit()
    
    return {
        "message": "Customer and all related records deleted successfully",
        "customer_id": customer_id
    }


@router.get("/{customer_id}/report", response_class=FileResponse)
async def generate_customer_report(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)

):
    """Generate PDF report for a customer (loans + installments)"""

    # Fetch customer
    result = await db.execute(select(Customer).filter(Customer.id == customer_id))

    customer = result.scalar_one_or_none()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Fetch loans
    loan_result = await db.execute(select(Loan).filter(Loan.customer_id == customer.id_number))
    loans = loan_result.scalars().all()

    # Fetch installments
    query = """
        SELECT i.id, i.amount, i.payment_date, l.id as loan_id
        FROM installments i
        JOIN loans l ON i.loan_id = l.id
        JOIN customers c ON l.customer_id = c.id_number
        WHERE c.id = :cid
        ORDER BY i.payment_date DESC
    """
    inst_result = await db.execute(text(query), {"cid": customer_id})
    installments = inst_result.fetchall()

    # Generate PDF with styled header and sections
    filename = f"customer_report_{customer.id}.pdf"
    filepath = os.path.join("reports", filename)
    os.makedirs("reports", exist_ok=True)

    c = canvas.Canvas(filepath, pagesize=A4)
    width, height = A4
    margin_x = 1 * inch
    y = height - 0.8 * inch

    # Top themed header bar
    c.setFillColor(colors.HexColor("#174064"))
    c.setStrokeColor(colors.HexColor("#174064"))
    c.rect(0, height - 1.1 * inch, width, 1.1 * inch, fill=1, stroke=0)

    # Title: bold and underlined
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    title = "COMPREHENSIVE LOAN REPORT"
    c.drawString(margin_x, height - 0.55 * inch, title)
    title_width = c.stringWidth(title, "Helvetica-Bold", 18)
    c.setStrokeColor(colors.white)
    c.setLineWidth(2)
    c.line(margin_x, height - 0.58 * inch, margin_x + title_width, height - 0.58 * inch)

    # Subtitle: customer name
    c.setFont("Helvetica", 11)
    c.drawString(margin_x, height - 0.9 * inch, f"Customer: {customer.name} (ID#: {customer.id_number})")

    # Reset drawing color for body
    c.setFillColor(colors.black)
    y = height - 1.4 * inch

    c.setFont("Helvetica", 12)
    c.drawString(1 * inch, y, f"ID Number: {customer.id_number}")
    y -= 0.25 * inch
    c.drawString(1 * inch, y, f"Phone: {customer.phone}")
    y -= 0.25 * inch
    c.drawString(1 * inch, y, f"Location: {customer.location or 'N/A'}")
    y -= 0.5 * inch

    # Section header helper
    def draw_section_header(label: str):
        nonlocal y
        if y < 1 * inch:
            c.showPage()
            c.setFillColor(colors.black)
            y = height - inch
        c.setFillColor(colors.HexColor("#E9F0F6"))
        c.setStrokeColor(colors.HexColor("#C5D6E5"))
        c.rect(margin_x - 0.1 * inch, y - 0.15 * inch, width - 2 * margin_x + 0.2 * inch, 0.4 * inch, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#174064"))
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margin_x, y, label)
        y -= 0.35 * inch
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 11)

    # Loans Section
    draw_section_header("Loans Summary")
    for loan in loans:
        if y < 1 * inch:
            c.showPage()
            c.setFillColor(colors.black)
            y = height - inch
        c.setFont("Helvetica", 11)
        c.drawString(margin_x, y, f"Loan ID: {loan.id}   Status: {loan.status.value}")
        y -= 0.18 * inch
        c.setFillColor(colors.HexColor("#2A6F3E"))
        c.drawString(margin_x, y, f"Amount: {loan.amount}")
        c.setFillColor(colors.black)
        c.drawString(margin_x + 2.5 * inch, y, f"Interest: {loan.interest_rate}%")
        y -= 0.18 * inch
        c.drawString(margin_x, y, f"Start: {loan.start_date}    Due: {loan.due_date}")
        y -= 0.22 * inch

    # Installments Section
    if y < 1 * inch:
        c.showPage()
        c.setFillColor(colors.black)
        y = height - inch
    draw_section_header("Recent Installments")

    if not installments:
        c.drawString(margin_x, y, "No installments available.")
    else:
        for i in installments:
            if y < 1 * inch:
                c.showPage()
                c.setFillColor(colors.black)
                y = height - inch
            c.setFont("Helvetica", 11)
            c.drawString(margin_x, y, f"Loan #{i.loan_id}")
            c.setFillColor(colors.HexColor("#2A6F3E"))
            c.drawString(margin_x + 1.6 * inch, y, f"Amount: {i.amount}")
            c.setFillColor(colors.black)
            # Convert payment_date from UTC to Africa/Nairobi
            payment_date_eat = i.payment_date.replace(tzinfo=ZoneInfo('UTC')).astimezone(ZoneInfo('Africa/Nairobi'))
            formatted_date = payment_date_eat.strftime("%d/%m/%Y %H:%M")
            c.drawString(margin_x + 3.6 * inch, y, f"Date: {formatted_date}")
            y -= 0.2 * inch

    c.save()

    return FileResponse(
        filepath,
        media_type="application/pdf",
        filename=filename
    )
