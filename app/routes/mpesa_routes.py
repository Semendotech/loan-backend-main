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
) -> None:
    """Classic multi-line payment log block for Render."""
    logger.info(
        "Payment Successfully Recorded:\n"
        f"     Customer: {customer_name}\n"
        f"            Loan: {loan_id}\n"
        f"          Amount: {amount}\n"
        f"       Remaining: {remaining}\n"
        f"          Status: {status}"
    )


class MpesaCallbackData(BaseModel):
    """Schema for M-Pesa C2B callback data"""
    TransID: str
    TransAmount: float
    MSISDN: str
    BillRefNumber: str


from ..utils.phone import normalize_phone as util_normalize_phone, hash_phone as util_hash_phone


async def send_sms(phone: str, message: str) -> bool:
    try:
        api_key = os.getenv("AFRICAS_TALKING_API_KEY")
        username = os.getenv("AFRICAS_TALKING_USERNAME")

        if not api_key:
            logger.error("AFRICAS_TALKING_API_KEY not configured")
            return False

        if not username:
            logger.error("AFRICAS_TALKING_USERNAME not configured")
            return False

        if not phone.startswith("+"):
            if phone.startswith("254"):
                phone = f"+{phone}"
            elif phone.startswith("0"):
                phone = f"+254{phone[1:]}"
            else:
                phone = f"+254{phone}"

        url = "https://api.africastalking.com/version1/messaging"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "apiKey": api_key
        }

        payload = {
            "username": username,
            "to": phone,
            "message": message,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, data=payload)

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


@router.post("/validation")
async def mpesa_validation(request: Request):
    try:
        body = await request.json()
        logger.debug("Validation request received: %s", body)
    except Exception as e:
        logger.warning(f"Validation request error: {str(e)}")

    return {
        "ResultCode": 0,
        "ResultDesc": "Validation successful"
    }


@router.post("/confirmation")
async def mpesa_confirmation(request: Request, db: AsyncSession = Depends(get_db)):
    timestamp = datetime.utcnow()

    try:
        body = await request.json()
        logger.debug("M-Pesa callback payload: %s", body)

        trans_id = body.get("TransID")
        amount = float(body.get("TransAmount") or 0)
        raw_msisdn = body.get("MSISDN", "")
        bill_ref = body.get("BillRefNumber") or ""

        is_hashed_msisdn = bool(re.fullmatch(r"[0-9a-fA-F]{64}", raw_msisdn))
        if is_hashed_msisdn:
            normalized_msisdn = None
            msisdn_hash = raw_msisdn.lower()
        else:
            try:
                normalized_msisdn = util_normalize_phone(raw_msisdn)
                msisdn_hash = util_hash_phone(normalized_msisdn)
            except ValueError as exc:
                logger.error(f"Invalid MSISDN in callback: {raw_msisdn} ({exc})")
                return {"ResultCode": 0, "ResultDesc": "Invalid phone number"}

        logger.debug(
            "Parsed callback TransID=%s amount=%s msisdn_hash=%s",
            trans_id,
            amount,
            f"{msisdn_hash[:16]}...",
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
            return {
                "ResultCode": 0,
                "ResultDesc": "Already processed"
            }

        result = await db.execute(
            select(models.Customer).where(models.Customer.phone_hash == msisdn_hash)
        )
        customer = result.scalar_one_or_none()

        if customer:
            logger.debug("Phone hash matched customer: %s", customer.name)
        else:
            logger.debug("No customer for phone hash %s...", msisdn_hash[:16])

        if not customer:
            logger.warning(
                f"UNMATCHED PAYMENT - No customer found for phone hash:\n"
                f"   TransID: {trans_id}\n"
                f"   Amount: {amount}\n"
                f"   MSISDN_Hash: {msisdn_hash}\n"
                f"   Timestamp: {timestamp}"
            )
            return {
                "ResultCode": 0,
                "ResultDesc": "Payment received - customer not found in system"
            }

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
            logger.warning(
                f"No matching loan found for customer:\n"
                f"   Customer: {customer.name}\n"
                f"   TransID: {trans_id}\n"
                f"   Amount: {amount}"
            )
            return {
                "ResultCode": 0,
                "ResultDesc": "Payment received - no matching loan found for customer"
            }

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
            loan_id=loan.id
        )
        db.add(mpesa_tx)

        await db.commit()

        _log_payment_recorded(
            customer_name=customer.name,
            loan_id=loan.id,
            amount=amount,
            remaining=loan.remaining_amount,
            status=_loan_status_label(loan),
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

        return {
            "ResultCode": 0,
            "ResultDesc": "Payment confirmed and recorded"
        }

    except Exception as e:
        await db.rollback()
        logger.error(f"Error processing payment: {str(e)}")
        return {
            "ResultCode": 0,
            "ResultDesc": "Payment received - processing error"
        }


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