from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from app.models import Customer, Guarantor, Loan
from app.services.loan_service import compute_weekly_progress, TOTAL_WEEKS

REPORT_DIR = os.path.join("reports", "loans")
HEADER_HEIGHT = 1.5 * inch
SECTION_GAP = 0.3 * inch


def _format_currency(value: float) -> str:
    return f"KSh {value:,.2f}"


def _ensure_space(c: canvas.Canvas, y: float, min_height: float, width: float) -> float:
    """Start a new page if there's not enough space for the next section."""
    if y - min_height <= inch:
        c.showPage()
        c.setFillColor(colors.white)
        c.setFont("Helvetica", 11)
        y = A4[1] - inch
    return y


def _draw_section_box(c: canvas.Canvas, label: str, x: float, y: float, width: float) -> float:
    y = _ensure_space(c, y, 0.6 * inch, width)
    c.setFillColor(colors.HexColor("#F3F6FA"))
    c.setStrokeColor(colors.HexColor("#D6E2F0"))
    c.roundRect(x, y - 0.25 * inch, width, 0.45 * inch, 6, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#174064"))
    c.setFont("Helvetica-Bold", 13)
    c.drawString(x + 0.15 * inch, y - 0.05 * inch, label.upper())
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 11)
    return y - 0.45 * inch


def _draw_key_value(
    c: canvas.Canvas,
    label: str,
    value: str,
    x: float,
    y: float,
) -> float:
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x, y, label.upper())
    c.setFont("Helvetica", 11)
    c.drawString(x, y - 0.16 * inch, value)
    return y - 0.32 * inch


def _draw_highlight_panel(
    c: canvas.Canvas,
    entries: list[tuple[str, str]],
    x: float,
    y: float,
    width: float,
) -> float:
    """Render a pill panel with quick stats."""
    height = 0.65 * inch
    y = _ensure_space(c, y, height, width)
    c.setFillColor(colors.HexColor("#102541"))
    c.roundRect(x, y - height, width, height, 10, fill=1, stroke=0)
    c.setFillColor(colors.white)
    segment_width = width / len(entries)
    for idx, (label, value) in enumerate(entries):
        seg_x = x + (idx * segment_width) + 0.2 * inch
        c.setFont("Helvetica-Bold", 12)
        c.drawString(seg_x, y - 0.28 * inch, value)
        c.setFont("Helvetica", 8.5)
        c.setFillColor(colors.HexColor("#B3C5EA"))
        c.drawString(seg_x, y - 0.48 * inch, label.upper())
        c.setFillColor(colors.white)
    return y - height - SECTION_GAP


def generate_loan_receipt(
    loan: Loan,
    *,
    customer: Optional[Customer] = None,
    guarantor: Optional[Guarantor] = None,
) -> Tuple[str, str]:
    """
    Build a PDF receipt summarizing the loan that was just issued.
    Returns a tuple of (file_path, filename).
    """
    customer = customer or loan.customer
    guarantor = guarantor or loan.guarantor

    if not customer:
        raise ValueError("Customer information is required to generate a loan receipt.")

    os.makedirs(REPORT_DIR, exist_ok=True)
    filename = f"loan_receipt_{loan.id}.pdf"
    filepath = os.path.join(REPORT_DIR, filename)

    c = canvas.Canvas(filepath, pagesize=A4)
    width, height = A4
    margin_x = 0.9 * inch
    body_width = width - (2 * margin_x)

    # Header band
    c.setFillColor(colors.HexColor("#0E2F52"))
    c.rect(0, height - HEADER_HEIGHT, width, HEADER_HEIGHT, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(margin_x, height - 0.75 * inch, "Loan Issuance Receipt")
    c.setFont("Helvetica", 11)
    c.drawString(
        margin_x,
        height - 0.98 * inch,
        f"Issued on {datetime.now(ZoneInfo('Africa/Nairobi')).strftime('%d %b %Y %H:%M')}",
    )
    c.setFont("Helvetica", 9.5)
    c.drawString(
        margin_x,
        height - 1.23 * inch,
        "Thank you for choosing our services. Please retain this document for records.",
    )

    c.setFillColor(colors.black)
    c.setFont("Helvetica", 11)
    y = height - HEADER_HEIGHT - SECTION_GAP

    schedule = compute_weekly_progress(loan)

    # Highlighted stats panel
    summary_entries = [
        ("Loan Number", str(loan.id)),
        ("Principal", _format_currency(loan.amount)),
        ("Total Payable", _format_currency(float(loan.total_amount))),
        ("Weekly Installment", _format_currency(schedule["weekly_due_amount"])),
    ]
    y = _draw_highlight_panel(c, summary_entries, margin_x, y, body_width)

    # Customer section
    y = _draw_section_box(c, "Customer Details", margin_x, y, body_width)
    y = _draw_key_value(c, "Name", customer.name, margin_x, y)
    y = _draw_key_value(c, "National ID", customer.id_number, margin_x, y)
    y = _draw_key_value(c, "Phone", customer.phone, margin_x, y)
    y = _draw_key_value(c, "Location", customer.location or "N/A", margin_x, y)
    y -= SECTION_GAP

    # Loan summary
    y = _draw_section_box(c, "Loan Summary", margin_x, y, body_width)
    y = _draw_key_value(c, "Start Date", str(loan.start_date), margin_x, y)
    y = _draw_key_value(c, "Due Date", str(loan.due_date), margin_x, y)
    y = _draw_key_value(c, "Interest Rate", f"{loan.interest_rate:.2f}%", margin_x, y)
    y = _draw_key_value(
        c,
        "Outstanding Balance",
        _format_currency(float(loan.remaining_amount or loan.total_amount)),
        margin_x,
        y,
    )
    y -= SECTION_GAP

    # Repayment schedule
    y = _draw_section_box(c, "Repayment Schedule", margin_x, y, body_width)
    y = _draw_key_value(
        c,
        f"Weekly Installment ({TOTAL_WEEKS} weeks)",
        _format_currency(schedule["weekly_due_amount"]),
        margin_x,
        y,
    )
    y = _draw_key_value(
        c,
        "Expected Paid So Far",
        _format_currency(schedule["expected_paid"]),
        margin_x,
        y,
    )
    y = _draw_key_value(
        c,
        "Actual Paid So Far",
        _format_currency(schedule["actual_paid"]),
        margin_x,
        y,
    )
    y = _draw_key_value(
        c,
        "Arrears / Outstanding",
        _format_currency(schedule["arrears_amount"]),
        margin_x,
        y,
    )
    y -= SECTION_GAP

    # Guarantor details if available
    if guarantor:
        y = _draw_section_box(c, "Guarantor", margin_x, y, body_width)
        y = _draw_key_value(c, "Name", guarantor.name, margin_x, y)
        y = _draw_key_value(c, "ID Number", guarantor.id_number, margin_x, y)
        y = _draw_key_value(c, "Phone", guarantor.phone, margin_x, y)
        y = _draw_key_value(c, "Relationship", guarantor.relationship or "N/A", margin_x, y)
        y = _draw_key_value(c, "Location", guarantor.location or "N/A", margin_x, y)

    c.setFillColor(colors.HexColor("#6B7280"))
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(
        margin_x,
        0.65 * inch,
        "This receipt is system-generated and valid without signature.",
    )

    c.save()
    return filepath, filename


