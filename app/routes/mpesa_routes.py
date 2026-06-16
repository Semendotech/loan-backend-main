from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
import logging
import httpx
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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/c2b", tags=["M-Pesa Integration"])


class MpesaCallbackData(BaseModel):
    """Schema for M-Pesa C2B callback data"""
    TransID: str
    TransAmount: float
    MSISDN: str
    BillRefNumber: str


def normalize_phone(phone: str) -> str:
    phone = ''.join(filter(str.isdigit, phone))
    if phone.startswith('254'):
        phone = '0' + phone[3:]
    elif not phone.startswith('0') and len(phone) == 9:
        phone = '0' + phone
    return phone


async def send_sms(phone: str, message: str) -> bool:
    """
    Send SMS via Africa's Talking API (Production).
    Returns True if successful, False otherwise.
    """
    try:
        api_key = os.getenv("AFRICAS_TALKING_API_KEY")
        username = os.getenv("AFRICAS_TALKING_USERNAME")

        if not api_key:
            logger.error("AFRICAS_TALKING_API_KEY not configured")
            return False

        if not username:
            logger.error("AFRICAS_TALKING_USERNAME not configured")
            return False

        # Ensure phone is in international format
        if not phone.startswith('+'):
            if phone.startswith('0'):
                phone = '+254' + phone[1:]
            else:
                phone = '+254' + phone

        # PRODUCTION ENDPOINT
        url = "https://api.africastalking.com/version1/messaging"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "apiKey": api_key
        }

        # NOTE: "from" (sender_id) is intentionally omitted so AT uses
        # the default shared shortcode. Add it back once an alphanumeric
        # sender ID has been approved on your Africa's Talking account.
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
    """
    Receive validation request from Safaricom C2B.
    """
    try:
        body = await request.json()
        logger.info(f"Validation request received: {body}")
    except Exception as e:
        logger.warning(f"Validation request error: {str(e)}")

    return {
        "ResultCode": 0,
        "ResultDesc": "Validation successful"
    }


@router.post("/confirmation")
async def mpesa_confirmation(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Receive C2B confirmation callback from Safaricom (JSON format).
    Matches customer by FirstName, sends SMS notification on successful payment.
    """
    timestamp = datetime.utcnow()

    try:
        # Read raw body as JSON
        body = await request.json()

        logger.info(f"CALLBACK RECEIVED: {body}")

        trans_id = body.get("TransID")
        amount = float(body.get("TransAmount") or 0)
        first_name = body.get("FirstName", "").upper()
        bill_ref = body.get("BillRefNumber") or ""

        logger.info(f"Extracted - TransID: {trans_id}, Amount: {amount}, FirstName: {first_name}")

        if not all([trans_id, first_name]):
            logger.error("Missing required fields")
            return {"ResultCode": 0, "ResultDesc": "Invalid data"}

        # Prevent duplicate processing
        existing = await db.execute(
            select(models.MpesaTransaction).where(models.MpesaTransaction.trans_id == trans_id)
        )
        if existing.scalar_one_or_none():
            logger.info(f"Duplicate transaction received: {trans_id}")
            return {
                "ResultCode": 0,
                "ResultDesc": "Already processed"
            }

        # Match customer by FirstName
        result = await db.execute(
            select(models.Customer).where(models.Customer.name.ilike(f"%{first_name}%"))
        )
        customers = result.scalars().all()

        logger.info(f"Found {len(customers)} customer(s) matching '{first_name}'")

        if not customers:
            logger.warning(
                f"UNMATCHED PAYMENT - Customer not found:\n"
                f"   TransID: {trans_id}\n"
                f"   Amount: {amount}\n"
                f"   FirstName: {first_name}\n"
                f"   Timestamp: {timestamp}"
            )
            return {
                "ResultCode": 0,
                "ResultDesc": "Payment received - customer not found in system"
            }

        if len(customers) > 1:
            logger.warning(
                f"DUPLICATE NAMES - Multiple matches for '{first_name}':\n"
                f"   TransID: {trans_id}\n"
                f"   Amount: {amount}\n"
                f"   Please review manually!"
            )
            return {
                "ResultCode": 0,
                "ResultDesc": "Payment received - multiple customers with same name, manual review needed"
            }

        customer = customers[0]
        logger.info(f"Customer found: {customer.name} (ID: {customer.id_number})")

        # Match ACTIVE, OVERDUE, or ARREARS loans
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

        logger.info(f"Loan found: Loan ID {loan.id}, Remaining: {loan.remaining_amount}")

        installment = models.Installment(
            loan_id=loan.id,
            amount=amount,
            payment_date=datetime.utcnow()
        )
        db.add(installment)

        loan.remaining_amount = max(0, loan.remaining_amount - amount)

        if loan.remaining_amount <= 0:
            loan.status = models.LoanStatus.COMPLETED
            loan.completed_at = datetime.utcnow()
            logger.info(f"LOAN COMPLETED: Loan ID {loan.id}, Customer: {customer.name}")
        else:
            logger.info(
                f"Partial payment recorded:\n"
                f"   Loan ID: {loan.id}\n"
                f"   Payment: {amount}\n"
                f"   Remaining: {loan.remaining_amount}"
            )

        # Record mpesa transaction to prevent duplicates
        mpesa_tx = models.MpesaTransaction(
            trans_id=trans_id,
            amount=amount,
            phone=customer.name,
            loan_id=loan.id
        )
        db.add(mpesa_tx)

        await db.commit()

        logger.info(
            f"Payment Successfully Recorded:\n"
            f"   Customer: {customer.name}\n"
            f"   Loan: {loan.id}\n"
            f"   Amount: {amount}\n"
            f"   Remaining: {loan.remaining_amount}\n"
            f"   Status: {loan.status.value}"
        )

        # Send SMS notification
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
                logger.info(f"SMS sent to {customer.phone}")
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
    """
    Register C2B Validation and Confirmation URLs with Safaricom.
    Uses Store Number 8158739 for receiving callbacks.
    """
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
    """
    Simulate a C2B payment via Safaricom API (sandbox/production depends on MPESA_BASE_URL).
    Used for testing only - not for production.
    """
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