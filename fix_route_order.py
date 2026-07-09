lines = open('app/routes/loan_routes.py', encoding='utf-8').readlines()
disbursed = lines[408:456]
rest = lines[:408] + lines[456:]
insert_at = next(i for i, l in enumerate(rest) if '@router.get("/{loan_id}"' in l)
final = rest[:insert_at] + disbursed + rest[insert_at:]
open('app/routes/loan_routes.py', 'w', encoding='utf-8').writelines(final)
print('Done. Disbursed moved to line', insert_at+1)
