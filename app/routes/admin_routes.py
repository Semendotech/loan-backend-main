import os
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.database import get_sync_db
from app.services.loan_service import LoanService

router = APIRouter(prefix="/admin", tags=["admin"])


def verify_sync_key(x_sync_key: str = Header(default=None)):
    expected = os.getenv("SYNC_SECRET_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Sync endpoint not configured")
    if not x_sync_key or x_sync_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing sync key")
    return True


@router.post("/sync")
def trigger_daily_sync(
    db: Session = Depends(get_sync_db),
    _auth: bool = Depends(verify_sync_key),
):
    """
    Run the daily loan sync: updates OVERDUE status, flags defaulters,
    creates arrears records as needed. Intended to be called on a schedule
    by an external cron service.
    """
    LoanService.daily_sync_all_loans(db)
    return {"message": "Sync completed successfully"}
