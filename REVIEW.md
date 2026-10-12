# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `health` / ~73 | Does not ping MongoDB | Stop DB; `GET /health` still returns 200 OK | Added `db.command("ping")` inside a try-except block, returning 503 on error. |
| 2 | `create_employee` / ~79 | Missing unique index, race condition | Concurrent identical `POST /employees` requests would create duplicate employees | Added `emp_code` unique index in `lifespan` and caught `DuplicateKeyError` to return 409. |
| 3 | `list_employees` / ~93 | Pagination logic skips page 1 | `?page=1&page_size=20` skips the first 20 records | Changed `skip = page * page_size` to `skip = (page - 1) * page_size`. |
| 4 | `list_employees` / ~94 | Total count ignores filters | `?department=HR` returns total DB count | Changed `count_documents({})` to `count_documents(q)`. |
| 5 | `list_employees` / ~95 | Does not sort by emp_code | `GET /employees` returns unordered data | Appended `.sort("emp_code", ASCENDING)` to the cursor. |
| 6 | `punch_in` / ~101 | Raises 500 TypeError if employee not found | Validating punch for an unknown `emp_code` | Added `if not emp: raise HTTPException(404, "Employee not found")`. |
| 7 | `punch_in` / ~102 | Uses naive local time instead of UTC | Server in a non-UTC timezone saves shifted time | Extracted logic to `truncate_to_seconds`, enforcing `tz=timezone.utc`. |
| 8 | `punch_in` / ~103 | Uses local calendar date, ignores overnight logic | Punch in at 01:00 AM IST for a 22:00-06:00 shift yields wrong date | Created `get_attendance_date` handling IST timezone and previous-day logic for overnight shifts. |
| 9 | `punch_in` / ~104 | Vulnerable to a check-then-insert race condition | Concurrent punch-ins yield multiple logs | Created unique index on `(emp_code, date)` and caught `DuplicateKeyError`. |
| 10 | `list_attendance` / ~144 | Loads entire collection into Python memory | OOM risk on large DBs, bypasses MongoDB pagination | Used DB cursor `.skip()`, `.limit()`, and `.sort()`. |
| 11 | `list_attendance` / ~145 | Sorting misses secondary `emp_code` sort | Ties have unstable sort order | Changed sorting to `[("date", DESCENDING), ("emp_code", ASCENDING)]`. |
| 12 | `compute_late_minutes` / ~37 | Calculates 0 for 10.016 mins (grace period missed) | Shift 09:30, punch 09:40:01 calculates 0 instead of 10 | Rewrote logic to `diff_seconds > 600`, correctly flooring `diff_seconds // 60`. |
| 13 | `compute_work_hours` / ~41 | Python `round()` uses banker's rounding | `2.125` rounds to `2.12` instead of `2.13` (R8) | Replaced with `Decimal` operations using `ROUND_HALF_UP`. |
| 14 | `compute_overtime` / ~47 | Ignores R3 >= 30 mins constraint | Punch out 20 mins after shift end returned 20 | Added `if minutes >= 30:` conditional before returning. |
| 15 | `EmployeeIn` / schemas | Missing shift validation | `shift_start == shift_end` | Added `@model_validator` to enforce `shift_start != shift_end`. |
| 16 | `PunchInIn` / schemas | Allows out of bound epoch millis | Invalid timestamp like 1783312500 (seconds) | Added `ge=100000000000` constraint on `punched_at`. |
| 17 | `list_attendance` / ~128 | date_from > date_to not validated | Passing invalid date ranges | Added manual format check and `date_from > date_to` raises 422. |
| 18 | `create_employee` / schemas | Inspected `emp_code` validation regex (`EMP\d{4,6}`) | Validated that correct `emp_code`s pass (Not a defect) | No changes required. |


Also note anything you looked at and decided was **not** a defect, and why.
