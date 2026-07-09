"""
Updates the sms_message block in app/routes/mpesa_routes.py with:
- Correct company name (Kodongo Savings and Credit)
- Loan due date
- Cleaner phrasing

Usage:
    python apply_sms_message_fix.py
"""
import shutil
import sys

TARGET_FILE = "app/routes/mpesa_routes.py"
BACKUP_FILE = TARGET_FILE + ".msg.bak"

OLD_BLOCK = '''        sms_message = (
            f"Payment received! KSh {amount:.2f} paid to Kodongo Trading Enterprises. "
            f"Your Outstanding loan balance is KSh {loan.remaining_amount:.2f} "
            f"on {payment_date} at {payment_time}. "
            f"For any inquiries call 0718016498."
        )'''

NEW_BLOCK = '''        due_date_str = loan.due_date.strftime("%d/%m/%Y") if loan.due_date else "N/A"
        sms_message = (
            f"KSh {amount:.2f} paid to Kodongo Savings and Credit on {payment_date} at {payment_time}. "
            f"Your loan balance is KSh {loan.remaining_amount:.2f}. "
            f"Due date: {due_date_str}. "
            f"For any inquiries call 0718016498."
        )'''


def main():
    with open(TARGET_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    if OLD_BLOCK not in content:
        print("ERROR: Could not find the exact old sms_message block. No changes made.")
        print("The file may have already been modified, or whitespace differs.")
        sys.exit(1)

    shutil.copy(TARGET_FILE, BACKUP_FILE)
    print(f"Backup written to {BACKUP_FILE}")

    new_content = content.replace(OLD_BLOCK, NEW_BLOCK, 1)

    with open(TARGET_FILE, "w", encoding="utf-8") as f:
        f.write(new_content)

    print("OK: Replaced sms_message block")
    print(f"Wrote updated {TARGET_FILE}")
    print("Now run: python -m py_compile " + TARGET_FILE)


if __name__ == "__main__":
    main()
