"""
Patch script: fixes two bugs in app/routes/dashboard_routes.py -> _calc_arrears()

Bug 1: backlog exceeding loan amount
  elapsed_days was uncapped calendar days since loan start, so a loan taken
  7 months ago was expected to have paid 200+ daily instalments even though
  its term is only ~30 days.

Bug 2: skipped days showing 0 while in arrears
  skipped_dates walked backward from today and stopped at the first day
  that was fully paid - so a payment made today hid the entire historical
  backlog.

Run from your loan-backend-clean folder:
    python fix_arrears.py
"""

import pathlib
import sys

TARGET = pathlib.Path("app/routes/dashboard_routes.py")

OLD = '''        elapsed_days = (today - start).days + 1
        expected_total = daily_instalment * elapsed_days
        paid_total = sum(sums_by_date.values())
        backlog = expected_total - paid_total

        if backlog <= 0.01:
            continue  # not behind, lifetime-cumulative

        # Full list of skipped dates (not just a count), consecutive run
        # ending today, never going before loan start.
        skipped_dates = []
        current = today
        while current >= start:
            paid = sums_by_date.get(current, 0.0)
            if paid < daily_instalment - 0.01:
                skipped_dates.append(str(current))
                current -= _td(days=1)
            else:
                break
        skipped_dates.reverse()  # oldest first'''

NEW = '''        due = loan.due_date.date() if isinstance(loan.due_date, _dt) else loan.due_date

        # Cap elapsed days at the loan's actual term (start -> due date),
        # so a loan that's months old doesn't keep accumulating expected
        # instalments past its own schedule.
        term_days = (due - start).days + 1 if due else None
        elapsed_days = (today - start).days + 1
        if term_days:
            elapsed_days = min(elapsed_days, term_days)

        expected_total = daily_instalment * elapsed_days
        # Safety net: expected can never exceed the total loan amount.
        expected_total = min(expected_total, loan.total_amount)

        paid_total = sum(sums_by_date.values())
        backlog = expected_total - paid_total

        if backlog <= 0.01:
            continue  # not behind, lifetime-cumulative

        # Every day within the loan's term where paid < daily instalment -
        # not just a trailing consecutive run, so a recent payment doesn't
        # mask an older unpaid gap.
        end_day = min(today, due) if due else today
        skipped_dates = []
        current = start
        while current <= end_day:
            paid = sums_by_date.get(current, 0.0)
            if paid < daily_instalment - 0.01:
                skipped_dates.append(str(current))
            current += _td(days=1)'''


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
    print("Backlog is now capped at the loan's term/total amount, and skipped")
    print("days now reflect every underpaid day in the term, not just a")
    print("trailing run from today.")


if __name__ == "__main__":
    main()
