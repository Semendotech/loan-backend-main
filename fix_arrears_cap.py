"""
Patch script: fixes backlog exceeding the loan's remaining balance in
app/routes/dashboard_routes.py -> _calc_arrears()

Root cause: paid_total was computed only from the Installment table sums.
If a payment updates loan.remaining_amount (e.g. via an M-Pesa callback)
without a matching Installment row landing in the date-bounded query,
the arrears calc thinks the customer paid nothing - so backlog can end
up equal to (or exceeding) the full loan amount even though the balance
has visibly gone down.

Fix:
- paid_total now takes the MAX of the Installment-sum and the authoritative
  (total_amount - remaining_amount) from the loan itself, so it never
  under-counts real payments.
- backlog is hard-capped at loan.remaining_amount. There are no penalties
  and the loan balance never grows past 30 days, so the cumulative
  deficit can never exceed what the customer actually still owes.

Run from your loan-backend-clean folder:
    python fix_arrears_cap.py
"""

import pathlib
import sys

TARGET = pathlib.Path("app/routes/dashboard_routes.py")

OLD = '''        paid_total = sum(sums_by_date.values())
        backlog = expected_total - paid_total
        if backlog <= 0.01:
            continue  # not behind, lifetime-cumulative'''

NEW = '''        paid_total_installments = sum(sums_by_date.values())
        # Authoritative paid amount, from the loan's own balance bookkeeping.
        # Covers payments that updated remaining_amount without leaving a
        # matching Installment row (e.g. some M-Pesa callback paths).
        paid_total_balance = loan.total_amount - loan.remaining_amount
        paid_total = max(paid_total_installments, paid_total_balance)

        backlog = expected_total - paid_total
        # Backlog can never exceed what the customer actually still owes -
        # there are no penalties and the loan balance itself never grows.
        backlog = min(backlog, loan.remaining_amount)

        if backlog <= 0.01:
            continue  # not behind, lifetime-cumulative'''


def main():
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found. Run this from loan-backend-clean folder.")
        sys.exit(1)

    text = TARGET.read_text(encoding="utf-8")

    if NEW in text:
        print("Already patched - nothing to do.")
        return

    if OLD not in text:
        print("ERROR: Could not find the expected block to replace.")
        print("The file may have changed since this patch was written.")
        print("No changes were made.")
        sys.exit(1)

    text = text.replace(OLD, NEW, 1)
    TARGET.write_text(text, encoding="utf-8")
    print(f"Patched {TARGET} successfully.")
    print("Backlog is now capped at the loan's remaining balance, and paid_total")
    print("uses the authoritative loan balance if it's ahead of the Installment sum.")


if __name__ == "__main__":
    main()
