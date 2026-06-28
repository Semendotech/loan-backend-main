from passlib.context import CryptContext
from datetime import datetime, timezone, timedelta

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

EAT = timezone(timedelta(hours=3))

def now_eat() -> datetime:
    """Return current datetime in East Africa Time (UTC+3)."""
    return datetime.now(tz=EAT)

def now_eat_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Return current EAT datetime as a formatted string."""
    return now_eat().strftime(fmt)

def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)
