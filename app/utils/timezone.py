"""
EAT (East Africa Time, UTC+3) helpers.
Kenya does not observe daylight saving, so this offset is always fixed.
"""
from datetime import datetime, timedelta

EAT_OFFSET = timedelta(hours=3)


def now_eat() -> datetime:
    """Current naive datetime in EAT (UTC+3)."""
    return datetime.utcnow() + EAT_OFFSET


def today_eat():
    """Current date in EAT."""
    return now_eat().date()
