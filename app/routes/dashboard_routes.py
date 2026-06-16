from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, and_, or_, text
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import List, Tuple
from fastapi.responses import FileResponse
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from ..database import get_db
from ..models import Loan, Customer, Arrears, LoanStatus, Installment
from ..auth import get_current_user
from ..services.loan_service import sync_overdue_state
from datetime import datetime, timedelta, time
from sqlalchemy import select, func

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


async def _refresh_overdue_states(db: AsyncSession):
    today = datetime.utcnow().date()
    result = await db.execute(
        select(Loan).filter(
            Loan.due_date.isnot(None),
            Loan.due_date < today,
            Loan.remaining_amount.isnot(None),
            Loan.remaining_amount > 0,
        )
    )
    loans = result.scalars().all()
    state_changed = False
    for loan in loans:
        state_changed = await sync_overdue_state(db, loan) or state_changed
    if state_changed:
        await db.commit()


@router.get("/metrics")
async def get_dashboard_metrics(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get dashboard metrics: active loans count and arrears count"""
    # await _refresh_overdue_states(db)
    # Active loans (active + overdue)
    active_statuses = [LoanStatus.ACTIVE, LoanStatus.OVERDUE]
    active_loans_count_res = await db.execute(
        select(func.count(Loan.id)).filter(Loan.status.in_(active_statuses))
    )
    active_loans = active_loans_count_res.scalar() or 0

    # Outstanding for active loans should be the sum of remaining_amount
    outstanding_res = await db.execute(
        select(func.coalesce(func.sum(Loan.remaining_amount), 0.0)).filter(Loan.status.in_(active_statuses))
    )
    active_loans_outstanding = float(outstanding_res.scalar() or 0.0)

    # Arrears counts and outstanding
    active_arrears_count_res = await db.execute(
        select(func.count(Arrears.id)).filter(Arrears.is_cleared == False)
    )
    active_arrears = active_arrears_count_res.scalar() or 0

    arrears_outstanding_res = await db.execute(
        select(func.coalesce(func.sum(Arrears.remaining_amount), 0.0)).filter(Arrears.is_cleared == False)
    )
    arrears_outstanding = float(arrears_outstanding_res.scalar() or 0.0)

    return {
        "active_loans": active_loans,
        "active_loans_outstanding": round(active_loans_outstanding, 2),
        "overdue_loans": active_arrears,
        "overdue_outstanding": round(arrears_outstanding, 2),
        # Backwards compatibility keys
        "active_arrears": active_arrears,
        "active_arrears_outstanding": round(arrears_outstanding, 2),
    }


def get_week_start_end(today: date) -> Tuple[date, date]:
    """Get the start (Sunday) and end (Saturday) of the calendar week for a given date."""
    # Get the day of the week (0 = Monday, 6 = Sunday)
    # We want Sunday = 0, so we adjust: (today.weekday() + 1) % 7
    days_since_sunday = (today.weekday() + 1) % 7
    week_start = today - timedelta(days=days_since_sunday)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


@router.get("/summary")
async def get_dashboard_summary(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    today = datetime.utcnow().date()

    # Month range
    month_start_date = today.replace(day=1)
    month_start_dt = datetime.combine(month_start_date, time.min)
    today_end_dt = datetime.combine(today, time.max)

    # Week range (Sunday → Saturday)
    week_start, week_end = get_week_start_end(today)
    week_start_dt = datetime.combine(week_start, time.min)
    week_end_dt = datetime.combine(week_end, time.max)

    # Last 3 months
    last3_start_date = today - timedelta(days=90)
    last3_start_dt = datetime.combine(last3_start_date, time.min)

    # -----------------------------
    # Completed loans amount (month)
    # -----------------------------
    completed_res = await db.execute(
        select(func.coalesce(func.sum(Loan.total_amount), 0.0))
        .where(
            Loan.status == LoanStatus.COMPLETED,
            Loan.completed_at.isnot(None),
            Loan.completed_at >= month_start_dt,
            Loan.completed_at <= today_end_dt,
        )
    )
    completed_loans_amount_this_month = float(completed_res.scalar() or 0.0)

    # -----------------------------
    # Active loans count (this month)
    # -----------------------------
    active_this_month_res = await db.execute(
        select(func.count(Loan.id))
        .where(
            Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.OVERDUE]),
            Loan.start_date >= month_start_date,
            Loan.start_date <= today,
        )
    )
    active_loans_count_this_month = int(active_this_month_res.scalar() or 0)

    # -----------------------------
    # Interest last 3 months
    # -----------------------------
    interest_res = await db.execute(
        select(func.coalesce(func.sum(Loan.total_amount - Loan.amount), 0.0))
        .where(
            Loan.status == LoanStatus.COMPLETED,
            Loan.completed_at.isnot(None),
            Loan.completed_at >= last3_start_dt,
            Loan.completed_at <= today_end_dt,
        )
    )
    interest_last_three_months = float(interest_res.scalar() or 0.0)

    # -----------------------------
    # Overdue records last 3 months
    # -----------------------------
    arrears_last3_res = await db.execute(
        select(func.count(Arrears.id))
        .where(
            Arrears.arrears_date >= last3_start_date,
            Arrears.arrears_date <= today,
        )
    )
    arrears_count_last_three_months = int(arrears_last3_res.scalar() or 0)

    # -----------------------------
    # Payments this week
    # -----------------------------
    weekly_payments_res = await db.execute(
        select(func.coalesce(func.sum(Installment.amount), 0.0))
        .where(
            Installment.payment_date >= week_start_dt,
            Installment.payment_date <= week_end_dt,
        )
    )
    total_paid_this_week = float(weekly_payments_res.scalar() or 0.0)

    # -----------------------------
    # Payments this month
    # -----------------------------
    monthly_payments_res = await db.execute(
        select(func.coalesce(func.sum(Installment.amount), 0.0))
        .where(
            Installment.payment_date >= month_start_dt,
            Installment.payment_date <= today_end_dt,
        )
    )
    total_paid_this_month = float(monthly_payments_res.scalar() or 0.0)

    # -----------------------------
    # Payments today
    # -----------------------------
    today_start_dt = datetime.combine(today, time.min)
    today_end_dt = datetime.combine(today, time.max)

    daily_payments_res = await db.execute(
        select(func.coalesce(func.sum(Installment.amount), 0.0))
        .where(
            Installment.payment_date >= today_start_dt,
            Installment.payment_date <= today_end_dt,
        )
    )
    total_paid_today = float(daily_payments_res.scalar() or 0.0)

    # -----------------------------
    # Response (UNCHANGED)
    # -----------------------------
    return {
        "completed_loans_amount_this_month": round(completed_loans_amount_this_month, 2),
        "active_loans_count_this_month": active_loans_count_this_month,
        "interest_last_three_months": round(interest_last_three_months, 2),
        "overdue_count_last_three_months": arrears_count_last_three_months,
        "arrears_count_last_three_months": arrears_count_last_three_months,
        "total_paid_today": round(total_paid_today, 2),
        "total_paid_this_week": round(total_paid_this_week, 2),
        "total_paid_this_month": round(total_paid_this_month, 2),
    }


@router.get("/trends")
async def get_trends(
    months: int = 3,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get returns and interest trends for the last N months"""
    
    # Calculate date range
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=months * 30)
    
    # Initialize result structure
    trends = []
    current = start_date
    
    while current <= end_date:
        # Get month start and end
        month_start = date(current.year, current.month, 1)
        if current.month == 12:
            month_end = date(current.year, 12, 31)
        else:
            month_end = date(current.year, current.month + 1, 1) - timedelta(days=1)
        
        # Get loans COMPLETED in this month
        loans_result = await db.execute(
            select(Loan).filter(
                and_(
                    Loan.status == LoanStatus.COMPLETED,
                    Loan.completed_at.isnot(None),
                    func.date(Loan.completed_at) >= month_start,
                    func.date(Loan.completed_at) <= month_end,
                )
            )
        )
        loans = loans_result.scalars().all()
        
        # Calculate returns/interest for completed loans only
        returns = sum(loan.total_amount for loan in loans)
        interest = sum((loan.total_amount - loan.amount) for loan in loans)
        
        trends.append({
            "month": current.strftime("%b"),
            "returns": round(returns, 2),
            "interest": round(interest, 2)
        })
        
        # Move to next month
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    
    return {
        "trends": trends
    }


@router.get("/recent-activity")
async def get_recent_activity(
    limit: int = 10,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get recent loans and payments"""
    
    # Get recent loans
    loans_result = await db.execute(
        select(Loan).order_by(Loan.created_at.desc()).limit(limit)
    )
    loans = loans_result.scalars().all()
    
    activities = []
    for loan in loans:
        activities.append({
            "type": "loan",
            "id": loan.id,
            "customer_id": loan.customer_id,
            "amount": loan.amount,
            "status": loan.status.value,
            "date": loan.created_at
        })
    
    return activities


@router.get("/payments-report", response_class=FileResponse)
async def download_payments_report(
    date_str: str | None = None,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a PDF listing all payments made on a specific date (defaults to today)."""
    eat_zone = ZoneInfo("Africa/Nairobi")
    target_date = datetime.now(eat_zone).date() if not date_str else datetime.strptime(date_str, "%Y-%m-%d").date()

    start_of_day_eat = datetime.combine(target_date, time.min, tzinfo=eat_zone)
    end_of_day_eat = datetime.combine(target_date, time.max, tzinfo=eat_zone)
    start_of_day_utc = start_of_day_eat.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    end_of_day_utc = end_of_day_eat.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    query = """
        SELECT 
            i.id as installment_id,
            i.amount as payment_amount,
            i.payment_date as payment_date,
            l.amount as principal_amount,
            l.total_amount as total_amount,
            l.remaining_amount as remaining_amount,
            c.name as customer_name,
            c.id_number as customer_id_number,
            c.phone as customer_phone
        FROM installments i
        JOIN loans l ON i.loan_id = l.id
        JOIN customers c ON l.customer_id = c.id_number
        WHERE i.payment_date >= :start_utc
          AND i.payment_date <= :end_utc
        ORDER BY i.payment_date DESC
    """

    result = await db.execute(text(query), {"start_utc": start_of_day_utc, "end_utc": end_of_day_utc})
    rows = result.fetchall()

    filename = f"payments_{target_date.isoformat()}.pdf"
    filepath = os.path.join("reports", filename)
    os.makedirs("reports", exist_ok=True)

    c = canvas.Canvas(filepath, pagesize=A4)
    width, height = A4
    margin_x = 0.85 * inch
    y = height - 0.8 * inch

    # Header bar
    c.setFillColor(colors.HexColor("#0F172A"))
    c.rect(0, height - 1.0 * inch, width, 1.0 * inch, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    title = f"Payments Report"
    c.drawString(margin_x, height - 0.5 * inch, title)
    c.setFont("Helvetica", 11)
    c.drawString(margin_x, height - 0.75 * inch, f"Date: {target_date.strftime('%B %d, %Y')}")

    y = height - 1.3 * inch

    # Summary pills
    total_payments = sum(float(r.payment_amount or 0) for r in rows)
    pill_height = 0.45 * inch
    pill_width = (width - 2 * margin_x - 0.3 * inch) / 2

    def draw_pill(x, label, value, accent):
        nonlocal y
        c.setFillColor(colors.HexColor(accent))
        c.roundRect(x, y - pill_height, pill_width, pill_height, 8, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(x + pill_width / 2, y - 0.15 * inch, label)
        c.setFont("Helvetica-Bold", 13)
        c.drawCentredString(x + pill_width / 2, y - 0.32 * inch, value)

    draw_pill(margin_x, "Total Payments", f"KSh {total_payments:,.2f}", "#16A34A")
    draw_pill(margin_x + pill_width + 0.3 * inch, "Payments Count", str(len(rows)), "#1D4ED8")
    y -= pill_height + 0.35 * inch

    # Table headers
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(colors.HexColor("#0F172A"))
    headers = ["#", "Customer", "ID", "Phone", "Amount", "Time"]
    usable_width = width - 2 * margin_x
    widths = [0.35, 2.1, 1.1, 1.15, 1.0, 0.7]
    col_positions = [margin_x]
    for w in widths[:-1]:
        col_positions.append(col_positions[-1] + w * inch)
    col_positions.append(margin_x + usable_width)

    header_y = y
    c.setFillColor(colors.HexColor("#E2E8F0"))
    c.rect(margin_x - 0.08 * inch, header_y - 0.3 * inch, usable_width + 0.16 * inch, 0.35 * inch, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#0F172A"))
    for i, h in enumerate(headers):
        c.drawString(col_positions[i] + 0.05 * inch, header_y - 0.1 * inch, h)
    y = header_y - 0.55 * inch

    c.setFont("Helvetica", 9)
    line_height = 0.32 * inch
    row_number = 0
    for r in rows:
        row_number += 1
        if y - line_height < 1.0 * inch:
            c.showPage()
            y = height - inch
            c.setFont("Helvetica", 9)

        # Convert payment_date from UTC to Africa/Nairobi (UTC+3)
        payment_date_eat = r.payment_date.replace(tzinfo=ZoneInfo('UTC')).astimezone(ZoneInfo('Africa/Nairobi'))
        customer_name = (r.customer_name or "")[:26]
        customer_phone = (r.customer_phone or "-")[:12]
        values = [
            str(row_number),
            customer_name,
            r.customer_id_number,
            customer_phone,
            f"KSh {float(r.payment_amount or 0):,.2f}",
            payment_date_eat.strftime("%H:%M"),
        ]

        for i, v in enumerate(values):
            c.drawString(col_positions[i] + 0.05 * inch, y, v)
        y -= line_height

    if not rows:
        c.setFont("Helvetica-Oblique", 11)
        c.setFillColor(colors.HexColor("#6B7280"))
        c.drawString(margin_x, y, "No payments recorded for this date.")

    c.save()

    return FileResponse(
        filepath,
        media_type="application/pdf",
        filename=filename
    )


@router.get("/overdue-report", response_class=FileResponse)
async def download_overdue_report(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a PDF listing all active overdue balances."""
    eat_zone = ZoneInfo("Africa/Nairobi")

    query = """
        SELECT 
            a.id as arrears_id,
            a.original_amount as original_amount,
            a.remaining_amount as remaining_amount,
            a.arrears_date as arrears_date,
            l.id as loan_id,
            c.name as customer_name,
            c.id_number as customer_id_number,
            c.phone as customer_phone
        FROM arrears a
        JOIN loans l ON a.loan_id = l.id
        JOIN customers c ON a.customer_id = c.id_number
        WHERE a.is_cleared = false
        ORDER BY a.arrears_date ASC
    """

    result = await db.execute(text(query))
    rows = result.fetchall()

    filename = f"overdue_report_{datetime.now(eat_zone).date().isoformat()}.pdf"
    filepath = os.path.join("reports", filename)
    os.makedirs("reports", exist_ok=True)

    c = canvas.Canvas(filepath, pagesize=A4)
    width, height = A4
    margin_x = 0.85 * inch
    y = height - 0.8 * inch

    # Header bar
    c.setFillColor(colors.HexColor("#0F172A"))
    c.rect(0, height - 1.0 * inch, width, 1.0 * inch, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    title = f"Overdue Report"
    c.drawString(margin_x, height - 0.5 * inch, title)
    c.setFont("Helvetica", 11)
    c.drawString(margin_x, height - 0.75 * inch, f"Generated: {datetime.now(eat_zone).strftime('%B %d, %Y %H:%M')}")

    y = height - 1.3 * inch

    # Summary pills
    total_overdue = sum(float(r.remaining_amount or 0) for r in rows)
    pill_height = 0.45 * inch
    pill_width = (width - 2 * margin_x - 0.3 * inch) / 2

    def draw_pill(x, label, value, accent):
        nonlocal y
        c.setFillColor(colors.HexColor(accent))
        c.roundRect(x, y - pill_height, pill_width, pill_height, 8, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(x + pill_width / 2, y - 0.15 * inch, label)
        c.setFont("Helvetica-Bold", 13)
        c.drawCentredString(x + pill_width / 2, y - 0.32 * inch, value)

    draw_pill(margin_x, "Total Overdue", f"KSh {total_overdue:,.2f}", "#DC2626")
    draw_pill(margin_x + pill_width + 0.3 * inch, "Overdue Cases", str(len(rows)), "#9333EA")
    y -= pill_height + 0.35 * inch

    # Table headers
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(colors.HexColor("#0F172A"))
    headers = ["#", "Customer", "ID", "Phone", "Original", "Remaining", "Since"]
    usable_width = width - 2 * margin_x
    widths = [0.35, 1.9, 0.85, 1.0, 1.0, 1.1, 0.85]
    col_positions = [margin_x]
    for w in widths[:-1]:
        col_positions.append(col_positions[-1] + w * inch)
    col_positions.append(margin_x + usable_width)

    header_y = y
    c.setFillColor(colors.HexColor("#E2E8F0"))
    c.rect(margin_x - 0.08 * inch, header_y - 0.3 * inch, usable_width + 0.16 * inch, 0.35 * inch, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#0F172A"))
    for i, h in enumerate(headers):
        c.drawString(col_positions[i] + 0.05 * inch, header_y - 0.1 * inch, h)
    y = header_y - 0.55 * inch

    c.setFont("Helvetica", 8)
    line_height = 0.32 * inch
    row_number = 0
    for r in rows:
        row_number += 1
        if y - line_height < 1.0 * inch:
            c.showPage()
            y = height - inch
            c.setFont("Helvetica", 8)

        customer_name = (r.customer_name or "")[:18]
        customer_phone = (r.customer_phone or "-")[:12]
        arrears_date = r.arrears_date.strftime("%d/%m/%Y") if r.arrears_date else "-"
        values = [
            str(row_number),
            customer_name,
            r.customer_id_number,
            customer_phone,
            f"KSh {float(r.original_amount or 0):,.0f}",
            f"KSh {float(r.remaining_amount or 0):,.2f}",
            arrears_date,
        ]

        for i, v in enumerate(values):
            c.drawString(col_positions[i] + 0.05 * inch, y, v)
        y -= line_height

    if not rows:
        c.setFont("Helvetica-Oblique", 11)
        c.setFillColor(colors.HexColor("#6B7280"))
        c.drawString(margin_x, y, "No overdue balances. Great work!")

    c.save()

    return FileResponse(
        filepath,
        media_type="application/pdf",
        filename=filename
    )

