"""
Patch script: adds search support to GET /arrears in app/routes/dashboard_routes.py

Adds a `q` query param that filters by customer name, phone, or ID number
(case-insensitive substring match), applied before pagination so `total`
reflects the filtered count.

Run from your loan-backend-clean folder:
    python fix_arrears_search.py
"""

import pathlib
import sys

TARGET = pathlib.Path("app/routes/dashboard_routes.py")

OLD = '''    current_user: dict = Depends(get_current_user),
):
    """Get all customers with a lifetime cumulative payment backlog."""
    items = _calc_arrears(db)
    total = len(items)
    page = items[offset:offset + limit]
    return {
        "items": page,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
@router.get("/arrears-report")'''

NEW = '''    q: str = None,
    current_user: dict = Depends(get_current_user),
):
    """Get all customers with a lifetime cumulative payment backlog."""
    items = _calc_arrears(db)

    if q:
        needle = q.strip().lower()
        items = [
            r for r in items
            if needle in (r.get("customer_name") or "").lower()
            or needle in (r.get("customer_phone") or "").lower()
            or needle in (r.get("customer_id_number") or "").lower()
        ]

    total = len(items)
    page = items[offset:offset + limit]
    return {
        "items": page,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
@router.get("/arrears-report")'''


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
    print("GET /arrears now accepts a `q` param for name/phone/ID search.")


if __name__ == "__main__":
    main()
