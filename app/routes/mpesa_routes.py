from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
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
from app import models

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
    now = datetime.utcnow().isoformat(timespec="seconds")
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
    now = datetime.utcnow().isoformat(timespec="seconds")
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
    api_key = os.getenv("AFRICAS_TALKING_API_KEY")
    username = os.getenv("AFRICAS_TALKING_USERNAME")

    if not api_key:
        logger.error("AFRICAS_TALKING_API_KEY not configured")
        return False

    if not username:
        logger.error("AFRICAS_TALKING_USERNAME not configured")
        return False

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.africastalking.com/version1/messaging",
                headers={
                    "apiKey": api_key,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={
                    "username": username,
                    "to": phone,
                    "message": message,
                },
            )
            logger.info(f"SMS API Response Status: {response.status_code}")
            logger.info(f"SMS API Response: {response.text}")
            if response.status_code in [200, 201]:
                logger.info(f"SMS sent successfully to {phone}")
                return True
            else:
                logger.error(f"SMS failed to send to {phone}: {response.text}")
                return False
    except Exception as e:
        logger.error(f"Error sending SMS: {str(e)}")
        return False


class MpesaCallbackData(BaseModel):
    TransactionType: str = ""
    TransID: str = ""
    TransTime: str = ""
    TransAmount: str = ""
    BusinessShortCode: str = ""
    BillRefNumber: str = ""
    InvoiceNumber: str = ""
    OrgAccountBalance: str = ""
    ThirdPartyTransID: str = ""
    MSISDN: str = ""
    FirstName: str = ""
    MiddleName: str = ""
    LastName: str = ""


@router.post("/confirmation")
async def mpesa_confirmation(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        body = await request.body()
        raw = body.decode("utf-8")
        logger.debug("Raw callback body: %s", raw)

        data = json.loads(raw)

        trans_id = data.get("TransID", "")
        raw_msisdn = data.get("MSISDN", "")
        timestamp = data.get("TransTime", "")
        amount = float(data.get("TransAmount", 0))

        normalized_msisdn = None
        if raw_msisdn:
            digits = re.sub(r"\D", "", raw_msisdn)
            if digits.startswith("0") and len(digits) == 10:
                normalized_msisdn = "254" + digits[1:]
            elif digits.startswith("254") and len(digits) == 12:
                normalized_msisdn = digits
            elif digits.startswith("7") and len(digits) == 9:
                normalized_msisdn = "254" + digits

        msisdn_hash = (
            hashlib.sha256(normalized_msisdn.encode()).hexdigest()
            if normalized_msisdn
            else None
        )

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
            )
            db.add(unmatched_tx)
            await db.commit()
            return {"ResultCode": 0, "ResultDesc": "Payment received - no matching loan found for customer"}

        logger.debug("Loan matched: id=%s remaining=%s", loan.id, loan.remaining_amount)

        installment = models.Installment(
            loan_id=loan.id,
            amount=amount,
            payment_date=datetime.utcnow(),
            recorded_by="System",
            source="daraja",
        )
        db.add(installment)

        loan.remaining_amount = max(0, loan.remaining_amount - amount)

        if loan.remaining_amount <= 0:
            loan.status = models.LoanStatus.COMPLETED
            loan.completed_at = datetime.utcnow()

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

        payment_date = datetime.now().strftime("%d/%m/%Y")
        payment_time = datetime.now().strftime("%H:%M")
        sms_message = (
            f"Payment received! KSh {amount:.2f} paid to Kodongo Trading Enterprises. "
            f"Your Outstanding loan balance is KSh {loan.remaining_amount:.2f} "
            f"on {payment_date} at {payment_time}. "
            f"For any inquiries call 0718016498."
        )

        if customer.phone:
            sms_sent = await send_sms(customer.phone, sms_message)
            if sms_sent:
                logger.debug("SMS sent to %s", customer.phone)
            else:
                logger.warning(f"SMS failed for {customer.phone}, but payment was recorded")
        else:
            logger.warning(f"No phone number for customer {customer.name}, skipping SMS")

        return {"ResultCode": 0, "ResultDesc": "Payment confirmed and recorded"}

    except Exception as e:
        await db.rollback()
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
            "created_at": tx.created_at.isoformat() if tx.created_at else None,
        }
        for tx in transactions
    ]


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
