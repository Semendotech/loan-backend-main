from passlib.context import CryptContext
import hashlib

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def normalize_phone(phone: str) -> str:
    """
    Normalize a phone number to a consistent format.
    Converts international format (254...) to local format (0...).
    Returns: Phone in format 0XXXXXXXXX (10 digits with leading 0)
    Examples:
        "254712345678" -> "0712345678"
        "0712345678" -> "0712345678"
        "712345678" -> "0712345678"
    """
    # Extract only digits
    phone = ''.join(filter(str.isdigit, phone))
    
    # Convert international format to local
    if phone.startswith('254'):
        phone = '0' + phone[3:]
    # Handle cases without country code or leading zero
    elif not phone.startswith('0') and len(phone) == 9:
        phone = '0' + phone
    
    return phone

def hash_phone(phone: str) -> str:
    """
    Compute SHA-256 hash of a phone number (matching Safaricom's hashing).
    Automatically normalizes the phone first to ensure consistency.
    Returns: hex-encoded SHA-256 hash (64 characters)
    
    IMPORTANT: Always use normalized format. Safaricom hashes the actual
    phone number sent to them, which is typically in local format (0...).
    """
    normalized = normalize_phone(phone)
    return hashlib.sha256(normalized.encode()).hexdigest()

