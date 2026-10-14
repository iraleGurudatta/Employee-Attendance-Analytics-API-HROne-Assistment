# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
I created the following MongoDB indexes to improve query performance and protect data integrity:

A. **`employees.emp_code` — unique index:** Ensures every employee code is unique and supports fast employee lookups. It also prevents duplicate employee codes during concurrent requests.

B. **`employees.department` — single-field index:** Supports filtering employees by department.

C. **`employees.joined_on` — single-field index:** Supports queries that filter employees by joining date.

D. **`employees (department, joined_on)` — compound index:** Supports queries that filter by department and joining date together.

E. **`attendance_logs (emp_code, date)` — unique compound index:** Guarantees that an employee can have at most one attendance record per attendance date. It also supports lookups by employee code and date.

F. **`attendance_logs (date DESC, emp_code ASC)` — compound index:** Supports listing attendance records by date in descending order, with employee code as the secondary sort order.

G. **`attendance_logs (date ASC, late_minutes ASC)` — compound index:** Supports date-based queries involving late minutes.

H. **`attendance_logs.status` — single-field index:** Supports filtering attendance records by status, such as `PRESENT`, `WFH`, or `ON_DUTY`.

MongoDB creates these indexes during application startup using `create_index()`. Repeating these calls with the same index specifications is idempotent.

**Index considered and rejected:** I would avoid creating a separate single-field index on `attendance_logs.emp_code`, because the existing compound index `(emp_code, date)` can already support queries filtering by `emp_code` alone. The additional index would consume storage and add write overhead without providing enough benefit for that query pattern.Indexes should ultimately be validated using representative queries and `explain()` to confirm that MongoDB uses an appropriate index rather than performing an unnecessary collection scan.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.

If two identical punch-in requests come at the same time, both requests first check whether the employee exists. Then both calculate the attendance date and try to insert the attendance record into MongoDB.

I have created a unique compound index on (emp_code, date), which prevents duplicate attendance records for the same employee on the same date. So, only one request can insert the record successfully and return HTTP 201. The other request gets a DuplicateKeyError, which I catch and return as HTTP 409 with the message already punched in for this date.

This way, MongoDB handles the race condition using the unique index, and duplicate attendance records are prevented even when both requests arrive simultaneously.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?

The leaderboard uses standard competition ranking, so employees with the same total late minutes receive the same rank. The next rank is skipped, for example, 1, 2, 2, 4. The limit is applied after ranking, so all employees whose rank is less than or equal to the limit are returned, even if this means the response contains more employees than the specified limit.


4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
The department summary starts with the employees collection and uses a $lookup to match attendance records. This ensures employees who have no attendance logs are still included in the department's headcount. It counts employees whose joined_on date is on or before the end of the selected month, as required by R9.

5. **One thing you would change** if this had to serve 100x the data.

If the system had to handle 100 times more data, I would optimize the MongoDB queries and aggregation pipelines. I would check query performance using explain() and create or adjust indexes based on the actual query patterns. This would help reduce collection scans and improve response time. I would also use pagination to avoid returning too many records in a single request. My first priority would be to measure the bottlenecks before making changes.
