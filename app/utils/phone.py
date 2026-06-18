import hashlib
import re

PHONE_PATTERN = re.compile(r"^254\d{9,10}$")


def normalize_phone(phone: str) -> str:
    """
    Normalize a Kenyan phone number to international format (254XXXXXXXXX).

    Strips whitespace, dashes, and parentheses, removes a leading '+',
    converts 00254 to 254, and converts local 0XXXXXXXXX numbers to 254 format.
  """
    if not phone:
        raise ValueError(f"Invalid phone format: {phone}")

    cleaned = re.sub(r"[\s\-\(\)]", "", phone.strip())

    if cleaned.startswith("+"):
        cleaned = cleaned[1:]

    if cleaned.startswith("00254"):
        cleaned = "254" + cleaned[5:]

    if cleaned.startswith("0") and len(cleaned) == 10:
        cleaned = "254" + cleaned[1:]

    if not PHONE_PATTERN.match(cleaned):
        raise ValueError(f"Invalid phone format: {phone}")

    return cleaned


def hash_phone(phone: str) -> str:
    """
    Return the SHA-256 hex digest of a phone string.

    The caller should pass an already-normalized phone number.
    """
    return hashlib.sha256(phone.encode()).hexdigest()
