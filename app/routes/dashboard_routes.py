from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo
from typing import List, Tuple
from fastapi.responses import FileResponse
import os
import logging
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib import colors
from sqlalchemy.orm import selectinload
from ..database import get_db
from ..services.pdf_layout import create_canvas, ensure_space, start_body_y, PAGE_MARGIN
from ..models import Loan, Customer, Arrears, LoanStatus, Installment
from ..auth import get_current_user
from ..services.loan_service import sync_overdue_state
from ..services.defaulter_service import get_defaulters

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
    """
    Dashboard metrics using unified definition:
    - ACTIVE: status == ACTIVE (within 30 days since start_date)
    - OVERDUE: status == OVERDUE or (status == ACTIVE and >30 days since start_date)
    """
    today = datetime.utcnow().date()

    # ========== ACTIVE LOANS (status == ACTIVE) ==========
    active_loans_count_res = await db.execute(
        select(func.count(Loan.id)).filter(Loan.status == LoanStatus.ACTIVE)
    )
    active_loans = active_loans_count_res.scalar() or 0

    active_outstanding_res = await db.execute(
        select(func.coalesce(func.sum(Loan.remaining_amount), 0.0)).filter(Loan.status == LoanStatus.ACTIVE)
    )
    active_loans_outstanding = float(active_outstanding_res.scalar() or 0.0)

    # ========== OVERDUE LOANS (status == OVERDUE, tracked via Arrears) ==========
    overdue_count_res = await db.execute(
        select(func.count(Arrears.id)).filter(Arrears.is_cleared == False)
    )
    overdue_loans = overdue_count_res.scalar() or 0

    overdue_outstanding_res = await db.execute(
        select(func.coalesce(func.sum(Arrears.remaining_amount), 0.0)).filter(Arrears.is_cleared == False)
    )
    overdue_outstanding = float(overdue_outstanding_res.scalar() or 0.0)

    return {
        "active_loans": active_loans,
        "active_loans_outstanding": round(active_loans_outstanding, 2),
        "overdue_loans": overdue_loans,
        "overdue_outstanding": round(overdue_outstanding, 2),
        "active_arrears": overdue_loans,
        "active_arrears_outstanding": round(overdue_outstanding, 2),
    }


def get_week_start_end(today: date) -> Tuple[date, date]:
    days_since_sunday = (today.weekday() + 1) % 7
    week_start = today - timedelta(days=days_since_sunday)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def _build_utc_range(start_date: date | None, end_date: date | None) -> tuple[datetime | None, datetime | None]:
    if start_date is None and end_date is None:
        return None, None

    inclusive_start = start_date or end_date
    inclusive_end = end_date or start_date
    if inclusive_start is None or inclusive_end is None:
        return None, None

    start_dt = datetime.combine(inclusive_start, time.min, tzinfo=ZoneInfo("Africa/Nairobi")).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    end_dt = datetime.combine(inclusive_end, time.max, tzinfo=ZoneInfo("Africa/Nairobi")).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return start_dt, end_dt


def _payment_date_in_eat(payment_date: datetime) -> date:
    if payment_date.tzinfo is None:
        payment_date = payment_date.replace(tzinfo=ZoneInfo("UTC"))
    return payment_date.astimezone(ZoneInfo("Africa/Nairobi")).date()


def _count_skipped_days(start_date: date, daily_instalment: float, payments_by_date: dict[date, float], today: date) -> int:
    expected_total = 0.0
    paid_total = 0.0
    skipped_days = 0
    current = start_date
    while current <= today:
        expected_total += daily_instalment
        paid_total += payments_by_date.get(current, 0.0)
        if paid_total < expected_total:
            skipped_days += 1
        current += timedelta(days=1)
    return skipped_days


@router.get("/summary")
async def get_dashboard_summary(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    today = datetime.utcnow().date()

    month_start_date = today.replace(day=1)
    month_start_dt = datetime.combine(month_start_date, time.min)
    today_end_dt = datetime.combine(today, time.max)

    week_start, week_end = get_week_start_end(today)
    week_start_dt = datetime.combine(week_start, time.min)
    week_end_dt = datetime.combine(week_end, time.max)

    last3_start_date = today - timedelta(days=90)
    last3_start_dt = datetime.combine(last3_start_date, time.min)

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

    active_this_month_res = await db.execute(
        select(func.count(Loan.id))
        .where(
            Loan.status == LoanStatus.ACTIVE,  # Only ACTIVE
            Loan.start_date >= month_start_date,
            Loan.start_date <= today,
        )
    )
    active_loans_count_this_month = int(active_this_month_res.scalar() or 0)

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

    total_customers_res = await db.execute(
        select(func.count(Customer.id))
    )
    total_customers = int(total_customers_res.scalar() or 0)

    arrears_last3_res = await db.execute(
        select(func.count(Arrears.id))
        .where(
            Arrears.arrears_date >= last3_start_date,
            Arrears.arrears_date <= today,
        )
    )
    arrears_count_last_three_months = int(arrears_last3_res.scalar() or 0)

    weekly_payments_res = await db.execute(
        select(func.coalesce(func.sum(Installment.amount), 0.0))
        .where(
            Installment.payment_date >= week_start_dt,
            Installment.payment_date <= week_end_dt,
        )
    )
    total_paid_this_week = float(weekly_payments_res.scalar() or 0.0)

    monthly_payments_res = await db.execute(
        select(func.coalesce(func.sum(Installment.amount), 0.0))
        .where(
            Installment.payment_date >= month_start_dt,
            Installment.payment_date <= today_end_dt,
        )
    )
    total_paid_this_month = float(monthly_payments_res.scalar() or 0.0)

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

    return {
        "completed_loans_amount_this_month": round(completed_loans_amount_this_month, 2),
        "active_loans_count_this_month": active_loans_count_this_month,
        "interest_last_three_months": round(interest_last_three_months, 2),
        "total_customers": total_customers,
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
    try:
        months = max(1, min(months, 24))
        end_date = datetime.utcnow().date()
        start_date = end_date - timedelta(days=months * 30)
        trends = []
        current = date(start_date.year, start_date.month, 1)

        while current <= end_date:
            month_start_dt = datetime.combine(current, time.min)
            if current.month == 12:
                next_month = date(current.year + 1, 1, 1)
            else:
                next_month = date(current.year, current.month + 1, 1)
            next_month_dt = datetime.combine(next_month, time.min)

            loans_result = await db.execute(
                select(Loan.total_amount, Loan.amount).filter(
                    Loan.status == LoanStatus.COMPLETED,
                    Loan.completed_at.isnot(None),
                    Loan.completed_at >= month_start_dt,
                    Loan.completed_at < next_month_dt,
                )
            )
            loans = loans_result.all()

            returns = sum(row.total_amount for row in loans)
            interest = sum((row.total_amount - row.amount) for row in loans)

            trends.append({
                "month": current.strftime("%b"),
                "returns": round(returns, 2),
                "interest": round(interest, 2),
            })

            current = next_month

        return {"trends": trends}
    except Exception:
        logging.exception("Unhandled exception in /dashboard/trends")
        raise


@router.get("/recent-activity")
async def get_recent_activity(
    limit: int = 10,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
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


# Include all the PDF report endpoints (unchanged from original)
# payments-report, overdue-report, cleared-loans-report, defaulters, etc.
# These endpoints don't need changes as they use proper filtering already

@router.get("/payments-report", response_class=FileResponse)
async def download_payments_report(
    date_str: str | None = None,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import text
    
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
            i.recorded_by as recorded_by,
            i.source as source,
            l.amount as principal_amount,
            l.total_amount as total_amount,
            l.remaining_amount as remaining_amount,
            c.name as customer_name,
            c.id_number as customer_id_number,
            c.phone as customer_phone,
            l.id as loan_id,
            (l.total_amount - 
             COALESCE((
                SELECT SUM(i2.amount) 
                FROM installments i2 
                WHERE i2.loan_id = l.id 
                AND (i2.payment_date < i.payment_date 
                     OR (i2.payment_date = i.payment_date AND i2.id <= i.id))
             ), 0)) as balance_after_payment
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

    c = create_canvas(filepath)
    width, height = A4
    margin_x = PAGE_MARGIN
    y = start_body_y()

    c.setFillColor(colors.HexColor("#0F172A"))
    c.rect(0, height - 1.0 * inch, width, 1.0 * inch, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(margin_x, height - 0.5 * inch, "Payments Report")
    c.setFont("Helvetica", 11)
    c.drawString(margin_x, height - 0.75 * inch, f"Date: {target_date.strftime('%B %d, %Y')}")

    y = start_body_y()

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

    headers = ["#", "Customer", "ID", "Phone", "Amount", "Time", "Recorded By", "Balance"]
    usable_width = width - 2 * margin_x
    widths = [0.25, 1.45, 0.75, 0.95, 0.75, 0.55, 0.85, 0.9]
    col_positions = [margin_x]
    for w in widths[:-1]:
        col_positions.append(col_positions[-1] + w * inch)

    header_y = y
    c.setFillColor(colors.HexColor("#E2E8F0"))
    c.rect(margin_x - 0.08 * inch, header_y - 0.3 * inch, usable_width + 0.16 * inch, 0.35 * inch, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#0F172A"))
    c.setFont("Helvetica-Bold", 9)
    for i, h in enumerate(headers):
        c.drawString(col_positions[i] + 0.05 * inch, header_y - 0.1 * inch, h)
    y = header_y - 0.55 * inch

    c.setFont("Helvetica", 8)
    line_height = 0.32 * inch
    row_number = 0
    for r in rows:
        row_number += 1
        y = ensure_space(c, y, line_height)

        payment_date_eat = r.payment_date.replace(tzinfo=ZoneInfo('UTC')).astimezone(ZoneInfo('Africa/Nairobi'))
        customer_name = (r.customer_name or "")[:20]
        customer_phone = (r.customer_phone or "-")[:12]
        values = [
            str(row_number),
            customer_name,
            r.customer_id_number,
            customer_phone,
            f"KSh {float(r.payment_amount or 0):,.2f}",
            payment_date_eat.strftime("%H:%M"),
            (r.recorded_by or "System"),
            f"KSh {float(r.balance_after_payment or 0):,.2f}",
        ]

        for i, v in enumerate(values):
            c.drawString(col_positions[i] + 0.05 * inch, y, v)
        y -= line_height

    if not rows:
        y = ensure_space(c, y, 0.25 * inch)
        c.setFont("Helvetica-Oblique", 11)
        c.setFillColor(colors.HexColor("#6B7280"))
        c.drawString(margin_x, y, "No payments recorded for this date.")

    c.save()

    return FileResponse(filepath, media_type="application/pdf", filename=filename)


@router.get("/overdue-report", response_class=FileResponse)
async def download_overdue_report(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
):
    from sqlalchemy import text
    
    eat_zone = ZoneInfo("Africa/Nairobi")
    date_filter = False
    query_params: dict[str, date] = {}

    if start_date is not None or end_date is not None:
        if start_date is None:
            start_date = end_date
        elif end_date is None:
            end_date = start_date
        if start_date and end_date and start_date > end_date:
            raise HTTPException(status_code=400, detail="start_date cannot be after end_date")
        date_filter = True
        query_params = {"start_date": start_date, "end_date": end_date}

    query = """
        SELECT 
            a.id as arrears_id,
            a.original_amount as original_amount,
            a.remaining_amount as remaining_amount,
            a.arrears_date as arrears_date,
            c.name as customer_name,
            c.id_number as customer_id_number,
            c.phone as customer_phone
        FROM arrears a
        JOIN loans l ON a.loan_id = l.id
        JOIN customers c ON a.customer_id = c.id
        WHERE a.is_cleared = false
    """

    if date_filter:
        query += "\n        AND a.arrears_date >= :start_date\n        AND a.arrears_date <= :end_date"

    query += "\n        ORDER BY a.arrears_date ASC"

    result = await db.execute(text(query), query_params)
    rows = result.fetchall()

    if date_filter and start_date and end_date:
        range_suffix = start_date.isoformat() if start_date == end_date else f"{start_date.isoformat()}_{end_date.isoformat()}"
    else:
        range_suffix = datetime.now(eat_zone).date().isoformat()
    filename = f"overdue_report_{range_suffix}.pdf"
    filepath = os.path.join("reports", filename)
    os.makedirs("reports", exist_ok=True)

    c = create_canvas(filepath)
    width, height = A4
    margin_x = PAGE_MARGIN
    y = start_body_y()

    c.setFillColor(colors.HexColor("#0F172A"))
    c.rect(0, height - 1.0 * inch, width, 1.0 * inch, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(margin_x, height - 0.5 * inch, "Overdue Report")
    c.setFont("Helvetica", 11)
    c.drawString(margin_x, height - 0.75 * inch, f"Generated: {datetime.now(eat_zone).strftime('%B %d, %Y %H:%M')}")

    y = start_body_y()

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

    headers = ["#", "Customer", "ID", "Phone", "Original", "Remaining", "Since"]
    usable_width = width - 2 * margin_x
    widths = [0.35, 1.9, 0.85, 1.0, 1.0, 1.1, 0.85]
    col_positions = [margin_x]
    for w in widths[:-1]:
        col_positions.append(col_positions[-1] + w * inch)

    header_y = y
    c.setFillColor(colors.HexColor("#E2E8F0"))
    c.rect(margin_x - 0.08 * inch, header_y - 0.3 * inch, usable_width + 0.16 * inch, 0.35 * inch, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#0F172A"))
    c.setFont("Helvetica-Bold", 9)
    for i, h in enumerate(headers):
        c.drawString(col_positions[i] + 0.05 * inch, header_y - 0.1 * inch, h)
    y = header_y - 0.55 * inch

    c.setFont("Helvetica", 8)
    line_height = 0.32 * inch
    row_number = 0
    for r in rows:
        row_number += 1
        y = ensure_space(c, y, line_height)

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
        y = ensure_space(c, y, 0.25 * inch)
        c.setFont("Helvetica-Oblique", 11)
        c.setFillColor(colors.HexColor("#6B7280"))
        c.drawString(margin_x, y, "No overdue balances. Great work!")

    c.save()

    return FileResponse(filepath, media_type="application/pdf", filename=filename)


@router.get("/defaulters")
async def list_defaulters(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
):
    items = await get_defaulters(db, reference_date=end_date, min_loan_start_date=start_date)
    return {
        "items": items,
        "count": len(items),
    }
