import os
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks

from app.database import SyncSessionLocal
from app.services.loan_service import LoanService

router = APIRouter(prefix="/admin", tags=["admin"])

_sync_status = {"running": False, "last_started": None, "last_finished": None, "last_error": None}


def verify_sync_key(x_sync_key: str = Header(default=None)):
    expected = os.getenv("SYNC_SECRET_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Sync endpoint not configured")
    if not x_sync_key or x_sync_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing sync key")
    return True


def _run_sync_job():
    _sync_status["running"] = True
    _sync_status["last_started"] = datetime.utcnow().isoformat()
    _sync_status["last_error"] = None
    db = SyncSessionLocal()
    try:
        LoanService.daily_sync_all_loans(db)
        _sync_status["last_finished"] = datetime.utcnow().isoformat()
    except Exception as e:
        _sync_status["last_error"] = f"{type(e).__name__}: {e}"
        db.rollback()
    finally:
        db.close()
        _sync_status["running"] = False


@router.post("/sync")
def trigger_daily_sync(
    background_tasks: BackgroundTasks,
    _auth: bool = Depends(verify_sync_key),
):
    """
    Trigger the daily loan sync in the background. Returns immediately;
    use GET /admin/sync/status to check progress.
    """
    if _sync_status["running"]:
        return {"message": "Sync already running", "status": _sync_status}
    background_tasks.add_task(_run_sync_job)
    return {"message": "Sync started in background"}


@router.get("/sync/status")
def get_sync_status(_auth: bool = Depends(verify_sync_key)):
    return _sync_status
