from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from app.utils.timezone import now_eat
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
import logging
import httpx
import re
import base64
import os
import json
import hashlib
from pathlib import Path
from dotenv import load_dotenv
from app.database import get_db
from app.auth import get_current_user
from app.auth import get_current_user
from app import models
from app.utils import now_eat_str

# Load .env located at project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/c2b", tags=["M-Pesa Integration"])


def _loan_status_label(loan: models.Loan) -> str:
    status = loan.status
    return status.value if hasattr(status, "value") else str(status)


def _log_payment_recorded(
    *,
    customer_name: str,
    loan_id: int,
    amount: float,
    remaining: float,
    status: str,
    phone: str = "",
    trans_id: str = "",
) -> None:
    now = now_eat_str()
    print("", flush=True)
    print("========== PAYMENT RECEIVED ==========", flush=True)
    print("Time         : " + now + " UTC", flush=True)
    print("Customer     : " + customer_name, flush=True)
    print("Phone        : " + phone, flush=True)
    print("Amount       : KES " + str(round(amount, 2)), flush=True)
    print("Ref Number   : " + trans_id, flush=True)
    print("Loan Balance : KES " + str(round(remaining, 2)), flush=True)
    print("Status       : " + status, flush=True)
    print("======================================", flush=True)
    print("", flush=True)


def _log_unmatched_payment(
    *,
    trans_id: str,
    amount: float,
    phone: str,
    reason: str,
) -> None:
    now = now_eat_str()
    print("", flush=True)
    print("========== UNMATCHED PAYMENT ==========", flush=True)
    print("Time       : " + now + " UTC", flush=True)
    print("TransID    : " + trans_id, flush=True)
    print("Phone      : " + phone, flush=True)
    print("Amount     : KES " + str(round(amount, 2)), flush=True)
    print("Reason     : " + reason, flush=True)
    print("=======================================", flush=True)
    print("", flush=True)


async def send_sms(phone: str, message: str) -> bool:
    api_key = os.getenv("MOBITECH_API_KEY", "")
    username = os.getenv("MOBITECH_USERNAME", "")
    sender_id = os.getenv("MOBITECH_SENDER_ID", "FULL_CIRCLE")

    if not api_key:
        logger.error("MOBITECH_API_KEY not configured")
        return False
    if not username:
        logger.error("MOBITECH_USERNAME not configured")
        return False

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://app.mobitechtechnologies.com/sms/sendsms",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={
                    "api_key": api_key,
                    "username": username,
                    "sender_id": sender_id,
                    "message": message,
                    "phone": phone,
                },
            )
            logger.info(f"SMS API Response Status: {response.status_code}")
            logger.info(f"SMS API Response: {response.text}")
            if response.status_code in [200, 201]:
                logger.info(f"SMS sent successfully to {phone}")
                return True
            else:
                logger.error(f"SMS send failed: {response.text}")
                return False
    except Exception as e:
        logger.error(f"SMS send exception: {e}")
        return False

@router.post("/confirmation")
async def mpesa_confirmation(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        body = await request.body()
        raw = body.decode("utf-8")
        print(">>> MPESA CALLBACK RECEIVED", flush=True)
        print(">>> RAW BODY: " + raw[:200], flush=True)
        logger.debug("Raw callback body: %s", raw)

        data = json.loads(raw)

        trans_id = data.get("TransID", "")
        raw_msisdn = data.get("MSISDN", "")
        timestamp = data.get("TransTime", "")
        amount = float(data.get("TransAmount", 0))
        first_name = data.get("FirstName", "").strip()
        middle_name = data.get("MiddleName", "").strip()
        last_name = data.get("LastName", "").strip()
        sender_name = " ".join(filter(None, [first_name, middle_name, last_name])) or None
        print(">>> PARSED: trans_id=" + trans_id + " msisdn=" + raw_msisdn + " amount=" + str(amount), flush=True)

        normalized_msisdn = None
        if raw_msisdn:
            digits = re.sub(r"\D", "", raw_msisdn)
            if digits.startswith("0") and len(digits) == 10:
                normalized_msisdn = "254" + digits[1:]
            elif digits.startswith("254") and len(digits) == 12:
                normalized_msisdn = digits
            elif digits.startswith("7") and len(digits) == 9:
                normalized_msisdn = "254" + digits

        # If normalization failed, raw_msisdn may already be a SHA256 hash (C2B v2)
        if normalized_msisdn:
            msisdn_hash = hashlib.sha256(normalized_msisdn.encode()).hexdigest()
        elif raw_msisdn and len(raw_msisdn) == 64 and all(c in "0123456789abcdef" for c in raw_msisdn):
            msisdn_hash = raw_msisdn  # already a SHA256 hash from Safaricom
        else:
            msisdn_hash = None

        if not all([trans_id, msisdn_hash, amount > 0]):
            logger.error(
                f"Invalid callback data - TransID: {trans_id}, Amount: {amount}, "
                f"Raw_MSISDN: {raw_msisdn}, Normalized_MSISDN: {normalized_msisdn or 'NONE'}"
            )
            return {"ResultCode": 0, "ResultDesc": "Invalid callback data"}

        existing = await db.execute(
            select(models.MpesaTransaction).where(models.MpesaTransaction.trans_id == trans_id)
        )
        if existing.scalar_one_or_none():
            logger.info("Duplicate transaction received: %s", trans_id)
            return {"ResultCode": 0, "ResultDesc": "Already processed"}

        result = await db.execute(
            select(models.Customer).where(models.Customer.phone_hash == msisdn_hash)
        )
        customer = result.scalar_one_or_none()

        if not customer:
            _log_unmatched_payment(
                trans_id=trans_id,
                amount=amount,
                phone=normalized_msisdn or raw_msisdn,
                reason="No customer found for phone number",
            )
            unmatched_tx = models.MpesaTransaction(
                trans_id=trans_id,
                amount=amount,
                phone=normalized_msisdn or raw_msisdn,
                loan_id=None,
                sender_name=sender_name,
            )
            db.add(unmatched_tx)
            await db.commit()
            return {"ResultCode": 0, "ResultDesc": "Payment received - customer not found in system"}

        logger.debug("Customer matched: %s (%s)", customer.name, customer.phone)

        result = await db.execute(
            select(models.Loan).where(
                (models.Loan.customer_id == customer.id_number) &
                (models.Loan.status.in_([
                    models.LoanStatus.ACTIVE,
                    models.LoanStatus.OVERDUE,
                    models.LoanStatus.ARREARS
                ]))
            )
        )
        loan = result.scalar_one_or_none()

        if not loan:
            _log_unmatched_payment(
                trans_id=trans_id,
                amount=amount,
                phone=customer.phone or normalized_msisdn or raw_msisdn,
                reason=f"Customer found ({customer.name}) but has no active loan",
            )
            unmatched_tx = models.MpesaTransaction(
                trans_id=trans_id,
                amount=amount,
                phone=customer.phone or normalized_msisdn or raw_msisdn,
                loan_id=None,
                sender_name=sender_name or customer.name,
            )
            db.add(unmatched_tx)
            await db.commit()
            return {"ResultCode": 0, "ResultDesc": "Payment received - no matching loan found for customer"}

        logger.debug("Loan matched: id=%s remaining=%s", loan.id, loan.remaining_amount)

        loan.remaining_amount = max(0, loan.remaining_amount - amount)
        installment = models.Installment(
            loan_id=loan.id,
            amount=amount,
            payment_date=now_eat(),
            recorded_by="System",
            source="daraja",
            balance_after=loan.remaining_amount,
        )
        db.add(installment)

        if loan.remaining_amount <= 0:
            loan.status = models.LoanStatus.COMPLETED
            loan.completed_at = now_eat()

        mpesa_tx = models.MpesaTransaction(
            trans_id=trans_id,
            amount=amount,
            phone=customer.phone,
            loan_id=loan.id,
        )
        db.add(mpesa_tx)

        await db.commit()

        _log_payment_recorded(
            customer_name=customer.name,
            loan_id=loan.id,
            amount=amount,
            remaining=loan.remaining_amount,
            status=_loan_status_label(loan),
            phone=customer.phone or "",
            trans_id=trans_id,
        )

        payment_date = now_eat_str("%d/%m/%Y")
        payment_time = now_eat_str("%H:%M")
        due_date_str = loan.due_date.strftime("%d/%m/%Y") if loan.due_date else "N/A"
        sms_message = (
            f"KSh {amount:.2f} paid to Kodongo Savings and Credit on {payment_date} at {payment_time}. "
            f"Your loan balance is KSh {loan.remaining_amount:.2f}. "
            f"Due date: {due_date_str}. "
            f"For any inquiries call 0718016498."
        )

        if customer.phone:
            print(f">>> ATTEMPTING SMS to {customer.phone}", flush=True)
            try:
                sms_sent = await send_sms(customer.phone, sms_message)
                print(f">>> SMS RESULT: {sms_sent}", flush=True)
            except Exception as sms_err:
                import traceback
                print(f">>> SMS EXCEPTION: {sms_err}", flush=True)
                print(traceback.format_exc(), flush=True)
                sms_sent = False
            if sms_sent:
                logger.debug("SMS sent to %s", customer.phone)
            else:
                logger.warning(f"SMS failed for {customer.phone}, but payment was recorded")
        else:
            print(f">>> NO PHONE for customer {customer.name}", flush=True)
            logger.warning(f"No phone number for customer {customer.name}, skipping SMS")

        return {"ResultCode": 0, "ResultDesc": "Payment confirmed and recorded"}

    except Exception as e:
        await db.rollback()
        import traceback
        print(">>> MPESA CALLBACK ERROR: " + str(e), flush=True)
        print(traceback.format_exc(), flush=True)
        logger.error(f"Error processing payment: {str(e)}")
        return {"ResultCode": 0, "ResultDesc": "Payment received - processing error"}


@router.get("/unmatched-payments")
async def get_unmatched_payments(db: AsyncSession = Depends(get_db)):
    """Return all MpesaTransactions where loan_id is NULL (unmatched payments)."""
    result = await db.execute(
        select(models.MpesaTransaction)
        .where(models.MpesaTransaction.loan_id == None)
        .order_by(models.MpesaTransaction.created_at.desc())
    )
    transactions = result.scalars().all()
    return [
        {
            "id": tx.id,
            "trans_id": tx.trans_id,
            "amount": tx.amount,
            "phone": tx.phone,
            "sender_name": tx.sender_name or "",
            "created_at": tx.created_at.isoformat() if tx.created_at else None,
        }
        for tx in transactions
    ]


@router.get("/unmatched-payments-pdf")
async def get_unmatched_payments_pdf(db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from io import BytesIO
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    from fastapi.responses import StreamingResponse
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.enums import TA_RIGHT, TA_CENTER

    EAT = ZoneInfo("Africa/Nairobi")
    now_eat = _dt.now(EAT).strftime("%d %b %Y, %H:%M")

    result = await db.execute(
        select(models.MpesaTransaction)
        .where(models.MpesaTransaction.loan_id == None)
        .order_by(models.MpesaTransaction.created_at.desc())
    )
    transactions = result.scalars().all()
    total = sum(float(tx.amount or 0) for tx in transactions)

    NAVY  = colors.HexColor("#0f2942")
    SLATE = colors.HexColor("#475569")
    LIGHT = colors.HexColor("#f8fafc")
    BORDER= colors.HexColor("#cbd5e1")
    GOLD  = colors.HexColor("#c9a84c")
    RED   = colors.HexColor("#dc2626")

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=14*mm, bottomMargin=14*mm, leftMargin=18*mm, rightMargin=18*mm)
    base = getSampleStyleSheet()
    inst_style  = ParagraphStyle("I", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=17, textColor=NAVY)
    tag_style   = ParagraphStyle("T", parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8, textColor=GOLD)
    rt_style    = ParagraphStyle("R", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9, textColor=NAVY, alignment=TA_RIGHT)
    rs_style    = ParagraphStyle("S", parent=base["Normal"], fontName="Helvetica", fontSize=8, textColor=SLATE, alignment=TA_RIGHT)
    sl_style    = ParagraphStyle("SL", parent=base["Normal"], fontName="Helvetica", fontSize=7.5, textColor=SLATE, alignment=TA_CENTER)
    sv_style    = ParagraphStyle("SV", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=13, textColor=NAVY, alignment=TA_CENTER)
    ftr_style   = ParagraphStyle("F", parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=7, textColor=SLATE, alignment=TA_CENTER)

    story = []
    left_tbl = Table([[Paragraph("KODONGO SAVINGS & CREDIT", inst_style)],[Paragraph("Trusted Financial Solutions", tag_style)]], colWidths=[None])
    left_tbl.setStyle(TableStyle([("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),2)]))
    right_tbl = Table([[Paragraph("UNMATCHED PAYMENTS", rt_style)],[Paragraph(f"Generated: {now_eat} EAT", rs_style)]], colWidths=[None])
    right_tbl.setStyle(TableStyle([("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),2)]))
    hdr = Table([[left_tbl, right_tbl]], colWidths=["60%","40%"])
    hdr.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0)]))
    story.append(hdr)
    story.append(Spacer(1,5))
    story.append(HRFlowable(width="100%", thickness=2.5, color=NAVY, spaceAfter=2))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD, spaceAfter=10))

    sum_tbl = Table([[Paragraph("TOTAL UNMATCHED", sl_style), Paragraph("TOTAL AMOUNT", sl_style)],[Paragraph(str(len(transactions)), sv_style), Paragraph(f"KES {total:,.2f}", sv_style)]], colWidths=["30%","70%"])
    sum_tbl.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.75,BORDER),("LINEAFTER",(0,0),(0,-1),0.5,BORDER),("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6)]))
    story.append(sum_tbl)
    story.append(Spacer(1,14))

    if not transactions:
        story.append(Paragraph("No unmatched payments on record.", base["Normal"]))
    else:
        rows = [["#", "TRANSACTION ID", "SENDER NAME", "PHONE", "AMOUNT (KES)", "DATE", "TIME"]]
        for idx, tx in enumerate(transactions, 1):
            phone_display = tx.phone if (tx.phone and len(tx.phone) != 64) else "Unknown"
            date_str = tx.created_at.strftime("%d/%m/%Y") if tx.created_at else "-"
            time_str = tx.created_at.strftime("%H:%M") if tx.created_at else "-"
            rows.append([str(idx), tx.trans_id, tx.sender_name or "-", phone_display, f"{float(tx.amount):,.2f}", date_str, time_str])

        tbl = Table(rows, repeatRows=1, colWidths=[8*mm, 28*mm, 40*mm, 30*mm, 25*mm, 20*mm, 15*mm])
        tbl.setStyle(TableStyle([
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTNAME",(0,1),(-1,-1),"Helvetica"),
            ("FONTSIZE",(0,0),(-1,-1),7.5),("TEXTCOLOR",(0,0),(-1,0),SLATE),
            ("BACKGROUND",(0,0),(-1,0),LIGHT),("ALIGN",(4,0),(4,-1),"RIGHT"),
            ("ALIGN",(0,0),(0,-1),"CENTER"),("LINEBELOW",(0,0),(-1,0),0.75,BORDER),
            ("LINEBELOW",(0,1),(-1,-2),0.35,BORDER),("BOX",(0,0),(-1,-1),0.75,BORDER),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,LIGHT]),
            ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
            ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
        ]))
        story.append(tbl)

    story.append(Spacer(1,18))
    story.append(HRFlowable(width="100%", thickness=0.75, color=BORDER, spaceAfter=6))
    story.append(Paragraph(f"Generated on {now_eat} EAT. Kodongo Savings & Credit.", ftr_style))
    doc.build(story)
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=unmatched_payments_{_dt.now(EAT).strftime('%Y-%m-%d')}.pdf"})


@router.post("/register-urls")
async def register_urls():
    consumer_key = os.getenv("MPESA_CONSUMER_KEY")
    consumer_secret = os.getenv("MPESA_CONSUMER_SECRET")
    shortcode = "8158739"
    base_url = os.getenv("MPESA_BASE_URL", "https://api.safaricom.co.ke")

    credentials = base64.b64encode(
        f"{consumer_key}:{consumer_secret}".encode()
    ).decode()

    async with httpx.AsyncClient() as client:
        token_response = await client.get(
            f"{base_url}/oauth/v1/generate?grant_type=client_credentials",
            headers={"Authorization": f"Basic {credentials}"}
        )
        token_data = token_response.json()
        access_token = token_data.get("access_token")

        logger.info("Got Safaricom access token successfully")

        confirmation_url = os.getenv("MPESA_CONFIRMATION_URL")
        validation_url = os.getenv("MPESA_VALIDATION_URL")

        register_response = await client.post(
            f"{base_url}/mpesa/c2b/v2/registerurl",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            },
            json={
                "ShortCode": shortcode,
                "ResponseType": "Completed",
                "ConfirmationURL": confirmation_url,
                "ValidationURL": validation_url
            }
        )
        result = register_response.json()
        logger.info(f"URL Registration response: {result}")
        return result


@router.post("/simulate")
async def simulate_payment():
    consumer_key = os.getenv("MPESA_CONSUMER_KEY")
    consumer_secret = os.getenv("MPESA_CONSUMER_SECRET")
    shortcode = os.getenv("MPESA_SHORTCODE")
    phone = os.getenv("MPESA_TEST_MSISDN", "254714269319")
    amount = float(os.getenv("MPESA_TEST_AMOUNT", "100"))
    base_url = os.getenv("MPESA_BASE_URL", "https://api.safaricom.co.ke")

    credentials = base64.b64encode(
        f"{consumer_key}:{consumer_secret}".encode()
    ).decode()

    async with httpx.AsyncClient() as client:
        token_response = await client.get(
            f"{base_url}/oauth/v1/generate?grant_type=client_credentials",
            headers={"Authorization": f"Basic {credentials}"}
        )
        token_data = token_response.json()
        access_token = token_data.get("access_token")

        simulate_response = await client.post(
            f"{base_url}/mpesa/c2b/v2/simulate",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            },
            json={
                "ShortCode": shortcode,
                "CommandID": "CustomerBuyGoodsOnline",
                "Amount": amount,
                "Msisdn": phone,
                "BillRefNumber": "0"
            }
        )
        result = simulate_response.json()
        logger.info(f"Simulate response: {result}")
        return result


