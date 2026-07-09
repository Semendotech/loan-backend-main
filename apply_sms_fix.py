"""
Safely replaces the send_sms function in app/routes/mpesa_routes.py
with the corrected Mobitech API version.

Usage:
    python apply_sms_fix.py
"""
import re
import shutil
import sys

TARGET_FILE = "app/routes/mpesa_routes.py"
BACKUP_FILE = TARGET_FILE + ".sms.bak"

NEW_FUNCTION = '''async def send_sms(phone: str, message: str) -> bool:
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
'''

def main():
    with open(TARGET_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    shutil.copy(TARGET_FILE, BACKUP_FILE)
    print(f"Backup written to {BACKUP_FILE}")

    # Match from "async def send_sms(" up to (but not including) the next
    # top-level "async def " / "def " / "@router" / "@app" at column 0.
    pattern = re.compile(
        r"async def send_sms\(.*?\n(?:.*\n)*?(?=\nasync def |\ndef |\n@router|\n@app|\Z)",
        re.MULTILINE,
    )

    match = pattern.search(content)
    if not match:
        print("ERROR: Could not locate send_sms function. No changes made.")
        sys.exit(1)

    old_block = match.group(0)
    print(f"Found send_sms function ({len(old_block)} chars). Replacing...")

    new_content = content[: match.start()] + NEW_FUNCTION + content[match.end():]

    with open(TARGET_FILE, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"OK: Replaced send_sms ({len(old_block)} chars -> {len(NEW_FUNCTION)} chars)")
    print(f"Wrote updated {TARGET_FILE}")
    print("Now run: python -m py_compile " + TARGET_FILE)


if __name__ == "__main__":
    main()
