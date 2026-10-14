"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import calendar
import os
from datetime import  datetime, timedelta, timezone, date as dt_date
from typing import Optional
from enum import Enum

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError
from contextlib import asynccontextmanager
from decimal import Decimal, ROUND_HALF_UP





load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))



@asynccontextmanager
async def lifespan(app: FastAPI):
    db.employees.create_index("emp_code", unique=True)
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True)
    db.employees.create_index("department")
    db.employees.create_index("joined_on")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)])
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)])
    db.attendance_logs.create_index([("date", ASCENDING), ("late_minutes", ASCENDING)])
    db.attendance_logs.create_index("status")

    yield
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0",lifespan=lifespan)
# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def truncate_to_seconds(epoch_ms: int) -> datetime:
    return datetime.fromtimestamp(epoch_ms // 1000, tz=timezone.utc)


def get_attendance_date(punch_in_utc: datetime, shift_start: str, shift_end: str) -> str:
    punch_in_ist = punch_in_utc.astimezone(IST)
    date_ist = punch_in_ist.date()
    
    if shift_end <= shift_start:
        if punch_in_ist.time() < datetime.strptime(shift_end, "%H:%M").time():
            date_ist -= timedelta(days=1)
            
    return date_ist.isoformat()


def compute_late_minutes(punch_in_utc: datetime, shift_start: str, date_str: str) -> int:
    h, m = map(int, shift_start.split(":"))
    shift_start_ist = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    
    diff_seconds = (punch_in_utc - shift_start_ist).total_seconds()
    if diff_seconds > 600:
        return int(diff_seconds // 60)
    return 0


def compute_work_hours(punch_in_utc: datetime, punch_out_utc: datetime) -> float:
    diff_seconds = (punch_out_utc - punch_in_utc).total_seconds()
    hours = Decimal(str(diff_seconds)) / Decimal('3600')
    return float(hours.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out_utc: datetime, shift_start: str, shift_end: str, date_str: str) -> int:
    """R3: Overtime calculated only if >= 30 mins."""
    h, m = map(int, shift_end.split(":"))
    shift_end_ist = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    if shift_end <= shift_start:
        shift_end_ist += timedelta(days=1)
        
    diff_seconds = (punch_out_utc - shift_end_ist).total_seconds()
    if diff_seconds >= 0:
        minutes = int(diff_seconds // 60)
        if minutes >= 30:
            return minutes
    return 0


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def derive(status: str, punch_in: Optional[datetime], punch_out: Optional[datetime],
           employee: dict, date_str: str) -> dict:
    if status in NO_PUNCH_STATUSES:
        return {"late_minutes": 0, "work_hours": None, "overtime_minutes": 0, "half_day": False}

    late_minutes = compute_late_minutes(punch_in, employee["shift_start"], date_str)
    if punch_out is None:
        return {"late_minutes": late_minutes, "work_hours": None, "overtime_minutes": 0, "half_day": False}

    work_hours = compute_work_hours(punch_in, punch_out)
    return {
        "late_minutes": late_minutes,
        "work_hours": work_hours,
        "overtime_minutes": compute_overtime(punch_out, employee["shift_start"], employee["shift_end"], date_str),
        "half_day": work_hours < 4.50,
    }
MAX_SHIFT = timedelta(hours=24)
NO_PUNCH_STATUSES = {"ABSENT", "LEAVE"}


PRESENT_STATUSES = ["PRESENT", "WFH", "ON_DUTY"]


def month_bounds(month: str) -> tuple[str, str]:
    try:
        start = datetime.strptime(month, "%Y-%m").date().replace(day=1)
    except ValueError:
        raise HTTPException(422, "month must be YYYY-MM")
    if start.strftime("%Y-%m") != month:
        raise HTTPException(422, "month must be YYYY-MM")
    end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
    return start.isoformat(), end.isoformat()


def rounded(value: Optional[float], places: int = 2) -> Optional[float]:
    if value is None:
        return None
    quantum = Decimal(1).scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def month_log_pipeline(emp_code: str, start: str, end: str) -> list[dict]:
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": start, "$lte": end}}},
        {"$set": {"weekday": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}}},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$status", PRESENT_STATUSES]}, {"$in": ["$weekday", [2, 3, 4, 5, 6]]}]},
                {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]}, 0]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
        }},
    ]


def working_days_pipeline(emp_code: str, start: str, end: str) -> list[dict]:
    days = (dt_date.fromisoformat(end) - dt_date.fromisoformat(start)).days + 1
    first_day = datetime.combine(dt_date.fromisoformat(start), datetime.min.time(), timezone.utc)
    return [
        {"$match": {"emp_code": emp_code}},
        {"$project": {"_id": 0, "working_days": {"$size": {"$filter": {
            "input": {"$map": {
                "input": {"$range": [0, days]}, "as": "offset",
                "in": {
                    "date": {"$dateToString": {"format": "%Y-%m-%d", "date": {
                        "$dateAdd": {"startDate": first_day, "unit": "day", "amount": "$$offset"}},
                        "timezone": "UTC"}},
                    "weekday": {"$dayOfWeek": {"$dateAdd": {
                        "startDate": first_day, "unit": "day", "amount": "$$offset"}}},
                },
            }},
            "as": "day",
            "cond": {"$and": [
                {"$gte": ["$$day.date", "$joined_on"]},
                {"$in": ["$$day.weekday", [2, 3, 4, 5, 6]]},
            ]},
        }}}}},
    ]


def department_summary_pipeline(start: str, end: str, department: Optional[str]) -> list[dict]:
    employee_match = {"joined_on": {"$lte": end}}
    if department is not None:
        employee_match["department"] = department
    return [
        {"$match": employee_match},
        {"$lookup": {
            "from": "attendance_logs", "let": {"code": "$emp_code"},
            "pipeline": [{"$match": {"$expr": {"$and": [
                {"$eq": ["$emp_code", "$$code"]}, {"$gte": ["$date", start]}, {"$lte": ["$date", end]},
            ]}}}], "as": "logs",
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$set": {"weekday": {"$cond": [
            {"$ne": [{"$ifNull": ["$logs.date", None]}, None]},
            {"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, None]}}},
        {"$group": {
            "_id": {"department": "$department", "emp_code": "$emp_code"},
            "present_days": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$logs.status", PRESENT_STATUSES]}, {"$in": ["$weekday", [2, 3, 4, 5, 6]]}]},
                {"$cond": [{"$ifNull": ["$logs.half_day", False]}, 0.5, 1]}, 0]}},
            "work_hours_sum": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$logs.status", PRESENT_STATUSES]}, {"$ne": ["$logs.work_hours", None]}]},
                "$logs.work_hours", 0]}},
            "work_hours_count": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$logs.status", PRESENT_STATUSES]}, {"$ne": ["$logs.work_hours", None]}]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}},
        }},
        {"$group": {
            "_id": "$_id.department", "headcount": {"$sum": 1},
            "present_days": {"$sum": "$present_days"}, "work_hours_sum": {"$sum": "$work_hours_sum"},
            "work_hours_count": {"$sum": "$work_hours_count"}, "late_count": {"$sum": "$late_count"},
            "total_late_minutes": {"$sum": "$total_late_minutes"}, "leave_count": {"$sum": "$leave_count"},
            "on_duty_count": {"$sum": "$on_duty_count"},
        }},
        {"$sort": {"_id": 1}},
    ]


def leaderboard_pipeline(start: str, end: str, department: Optional[str], limit: int) -> list[dict]:
    pipeline = [
        {"$match": {"date": {"$gte": start, "$lte": end}, "late_minutes": {"$gt": 0}}},
        {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}, "late_count": {"$sum": 1}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline.extend([
        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$project": {"_id": 0, "rank": 1, "emp_code": "$_id", "name": "$employee.name",
                      "department": "$employee.department", "total_late_minutes": 1, "late_count": 1}},
    ])
    return pipeline


def trend_pipeline(department: str, start: dt_date, end: dt_date) -> list[dict]:
    days = (end - start).days + 1
    weekday_expr = {"$dayOfWeek": {"$dateFromString": {"dateString": "$_id"}}}
    return [
        {"$match": {"department": department}},
        {"$project": {"emp_code": 1, "joined_on": 1}},
        {"$set": {"dates": {"$map": {
            "input": {"$range": [0, days]}, "as": "offset",
            "in": {"$dateToString": {"format": "%Y-%m-%d", "date": {
                "$dateAdd": {"startDate": datetime.combine(start, datetime.min.time(), timezone.utc),
                              "unit": "day", "amount": "$$offset"}}, "timezone": "UTC"}},
        }}}},
        {"$unwind": "$dates"},
        {"$lookup": {
            "from": "attendance_logs", "let": {"code": "$emp_code", "day": "$dates"},
            "pipeline": [{"$match": {"$expr": {"$and": [
                {"$eq": ["$emp_code", "$$code"]}, {"$eq": ["$date", "$$day"]},
            ]}}}], "as": "log",
        }},
        {"$unwind": {"path": "$log", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$dates",
            "headcount": {"$sum": {"$cond": [{"$lte": ["$joined_on", "$dates"]}, 1, 0]}},
            "present_count": {"$sum": {"$cond": [
                {"$in": ["$log.status", PRESENT_STATUSES]},
                {"$cond": [{"$ifNull": ["$log.half_day", False]}, 0.5, 1]}, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$log.late_minutes", 0]}, 0]}, 1, 0]}},
        }},
        {"$set": {
            "date": "$_id",
            "is_working_day": {"$in": [weekday_expr, [2, 3, 4, 5, 6]]},
            "attendance_rate": {"$cond": [
                {"$and": [{"$in": [weekday_expr, [2, 3, 4, 5, 6]]}, {"$gt": ["$headcount", 0]}]},
                {"$divide": ["$present_count", "$headcount"]}, None]},
        }},
        {"$setWindowFields": {"sortBy": {"date": 1}, "output": {
            "moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, "current"]}},
        }}},
        {"$project": {
            "_id": 0, "date": 1, "is_working_day": 1, "headcount": 1, "present_count": 1, "late_count": 1,
            "attendance_rate": {"$cond": [{"$eq": ["$attendance_rate", None]}, None,
                {"$divide": [{"$floor": {"$add": [{"$multiply": ["$attendance_rate", 10000]}, 0.5]}}, 10000]}]},
            "moving_avg_7d": {"$cond": [{"$eq": ["$moving_avg_7d", None]}, None,
                {"$divide": [{"$floor": {"$add": [{"$multiply": ["$moving_avg_7d", 10000]}, 0.5]}}, 10000]}]},
        }},
        {"$sort": {"date": 1}},
    ]


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = Field(default=None, ge=100000000000, le=4102444800000)
    status: str = "PRESENT"

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = Field(default=None, ge=100000000000, le=4102444800000)

class Status(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LEAVE = "LEAVE"
    WFH = "WFH"
    ON_DUTY = "ON_DUTY"


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Optional[Status] = None
    punch_in: Optional[StrictInt] = Field(default=None, ge=100000000000, le=4102444800000)
    punch_out: Optional[StrictInt] = Field(default=None, ge=100000000000, le=4102444800000)
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "database unavailable")

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(
    department: Optional[str] = None, 
    page: int = Query(1, ge=1), 
    page_size: int = Query(20, ge=1, le=100)
):
    q = {}
    if department:
        q["department"] = department
        
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    cursor = db.employees.find(q, {"_id": 0}).sort("emp_code", ASCENDING).skip(skip).limit(page_size)
    items = list(cursor)
    
    for item in items:
        if "created_at" in item:
            dt = item["created_at"]
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            item["created_at"] = int(dt.timestamp() * 1000)
            
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
        
    if body.punched_at is not None:
        ts_utc = truncate_to_seconds(body.punched_at)
    else:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        ts_utc = truncate_to_seconds(now_ms)
        
    d = get_attendance_date(ts_utc, emp["shift_start"], emp["shift_end"])
    
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts_utc,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts_utc, emp["shift_start"], d),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
        
    doc.pop("_id", None)
    doc["punch_in"] = int(doc["punch_in"].replace(tzinfo=timezone.utc).timestamp() * 1000)
    return doc

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[dt_date] = None,
    date_to: Optional[dt_date] = None,
    status: Optional[Status] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from > date_to")

    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from.isoformat()
        if date_to:
            q["date"]["$lte"] = date_to.isoformat()
    if status:
        q["status"] = status
        
    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    cursor = db.attendance_logs.find(q, {"_id": 0}).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip(skip).limit(page_size)
    items = list(cursor)
    
    items = [format_attendance_doc(i) for i in items]
            
    return {"items": items, "total": total, "page": page, "page_size": page_size}
def format_attendance_doc(item: dict) -> dict:
    for field in ["punch_in", "punch_out"]:
        if item.get(field):
            dt = item[field]
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            item[field] = int(dt.timestamp() * 1000)
            
    for he in item.get("history", []):
        if he.get("at"):
            dt = he["at"]
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            he["at"] = int(dt.timestamp() * 1000)
        
        changes = he.get("changes", {})
        for field in ["punch_in", "punch_out"]:
            if field in changes:
                for k in ["from", "to"]:
                    if changes[field].get(k):
                        dt = changes[field][k]
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        changes[field][k] = int(dt.timestamp() * 1000)
    return item

# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #


@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    ts_utc = truncate_to_seconds(body.punched_at if body.punched_at is not None else now_ms)

    # most recent record with punch_in <= punched_at (null punch_in never matches)
    log = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": ts_utc}},
        sort=[("date", DESCENDING)],
    )
    if not log:
        raise HTTPException(404, "No punch-in found")
    if log.get("punch_out") is not None:
        raise HTTPException(409, "already punched out")

    punch_in_utc = as_utc(log["punch_in"])
    if ts_utc <= punch_in_utc or ts_utc - punch_in_utc > MAX_SHIFT:
        raise HTTPException(422, "punched_at must be after punch_in and within 24 hours")

    d = derive(log["status"], punch_in_utc, ts_utc, emp, log["date"])
    updated = db.attendance_logs.find_one_and_update(
        {"_id": log["_id"], "punch_out": None},
        {"$set": {"punch_out": ts_utc, "work_hours": d["work_hours"],
                  "overtime_minutes": d["overtime_minutes"], "half_day": d["half_day"]}},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if updated is None:
        raise HTTPException(409, "already punched out")
    return format_attendance_doc(updated)


@app.patch("/attendance/{emp_code}/{date}", status_code=200)
def regularize_attendance(emp_code: str, date: dt_date, body: RegularizeIn):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
    date_str = date.isoformat()
    log = db.attendance_logs.find_one({"emp_code": emp_code, "date": date_str})
    if not log:
        raise HTTPException(404, "Attendance record not found")

    old = {
        "status": log["status"],
        "punch_in": as_utc(log.get("punch_in")),
        "punch_out": as_utc(log.get("punch_out")),
        "late_minutes": log.get("late_minutes", 0),
        "work_hours": log.get("work_hours"),
        "overtime_minutes": log.get("overtime_minutes", 0),
        "half_day": log.get("half_day", False),
    }

    new_status = body.status.value if body.status else old["status"]
    if new_status in NO_PUNCH_STATUSES:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, f"{new_status} cannot have punch times")
        new_in = new_out = None
    else:
        new_in = truncate_to_seconds(body.punch_in) if body.punch_in is not None else old["punch_in"]
        new_out = truncate_to_seconds(body.punch_out) if body.punch_out is not None else old["punch_out"]
        if new_in is None:
            raise HTTPException(422, "a presence status requires punch_in")
        if get_attendance_date(new_in, emp["shift_start"], emp["shift_end"]) != date_str:
            raise HTTPException(422, "punch_in must stay on the record's attendance date")
        if new_out is not None and (new_out <= new_in or new_out - new_in > MAX_SHIFT):
            raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")

    new = {"status": new_status, "punch_in": new_in, "punch_out": new_out,
           **derive(new_status, new_in, new_out, emp, date_str)}
    changes = {k: {"from": old[k], "to": new[k]} for k in new if new[k] != old[k]}
    if not changes:
        raise HTTPException(422, "request changes nothing")

    entry = {"at": datetime.now(timezone.utc), "by": body.regularized_by,
             "reason": body.reason, "changes": changes}
    history = log.get("history", [])
    history_guard = {"history": {"$size": len(history)}}
    if not history:
        history_guard = {"$or": [history_guard, {"history": {"$exists": False}}]}
    updated = db.attendance_logs.find_one_and_update(
        {"_id": log["_id"], "punch_in": log.get("punch_in"), "punch_out": log.get("punch_out"),
         **history_guard},
        {"$set": new, "$push": {"history": entry}},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if updated is None:
        raise HTTPException(409, "record was modified concurrently; retry")
    return format_attendance_doc(updated)


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
    if db.employees.find_one({"emp_code": emp_code}, {"_id": 1}) is None:
        raise HTTPException(404, "Employee not found")
    start, end = month_bounds(month)
    row = next(db.attendance_logs.aggregate(month_log_pipeline(emp_code, start, end)), {})
    working_day_row = next(db.employees.aggregate(working_days_pipeline(emp_code, start, end)), {})
    working_days = working_day_row.get("working_days", 0)
    present_days = row.get("present_days", 0)
    attendance_pct = rounded(present_days / working_days * 100) if working_days else None
    return {
        "emp_code": emp_code, "month": month, "working_days": working_days,
        "present_days": present_days, "leave_days": row.get("leave_days", 0),
        "late_count": row.get("late_count", 0), "total_late_minutes": row.get("total_late_minutes", 0),
        "total_overtime_minutes": row.get("total_overtime_minutes", 0), "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), department: Optional[str] = None,
):
    start, end = month_bounds(month)
    items = []
    for row in db.employees.aggregate(department_summary_pipeline(start, end, department)):
        hours_count = row["work_hours_count"]
        items.append({
            "department": row["_id"], "headcount": row["headcount"],
            "present_days": row["present_days"],
            "avg_work_hours": rounded(row["work_hours_sum"] / hours_count) if hours_count else None,
            "late_count": row["late_count"], "total_late_minutes": row["total_late_minutes"],
            "leave_count": row["leave_count"], "on_duty_count": row["on_duty_count"],
        })
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50), department: Optional[str] = None,
):
    start, end = month_bounds(month)
    items = list(db.attendance_logs.aggregate(leaderboard_pipeline(start, end, department, limit)))
    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_date: dt_date = Query(alias="from"), to_date: dt_date = Query(alias="to")):
    if to_date < from_date or (to_date - from_date).days + 1 > 92:
        raise HTTPException(422, "invalid date range")
    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(404, "Department not found")
    items = list(db.employees.aggregate(trend_pipeline(department, from_date, to_date)))
    return {"department": department, "items": items}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[dt_date] = None,
    date_to: Optional[dt_date] = None,
    status: Optional[Status] = None,
    from_date: Optional[dt_date] = Query(None, alias="from"),
    to_date: Optional[dt_date] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if endpoint not in {"attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"}:
        raise HTTPException(422, "unknown endpoint")
    if endpoint == "attendance_list":
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "date_from > date_to")
        query = {}
        if emp_code:
            query["emp_code"] = emp_code
        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from.isoformat()
            if date_to:
                query["date"]["$lte"] = date_to.isoformat()
        if status:
            query["status"] = status.value
        operation = {"find": "attendance_logs", "filter": query,
                     "sort": {"date": -1, "emp_code": 1}, "skip": (page - 1) * page_size, "limit": page_size}
        collection = "attendance_logs"
    else:
        if endpoint == "employee_monthly":
            if month is None or emp_code is None:
                raise HTTPException(422, "emp_code and month are required")
            start, end = month_bounds(month)
            collection = "attendance_logs"
            pipeline = month_log_pipeline(emp_code, start, end)
        elif endpoint == "department_summary":
            if month is None:
                raise HTTPException(422, "month is required")
            start, end = month_bounds(month)
            collection = "employees"
            pipeline = department_summary_pipeline(start, end, department)
        elif endpoint == "late_leaderboard":
            if month is None:
                raise HTTPException(422, "month is required")
            start, end = month_bounds(month)
            collection = "attendance_logs"
            pipeline = leaderboard_pipeline(start, end, department, limit)
        else:
            if department is None or from_date is None or to_date is None:
                raise HTTPException(422, "department, from, and to are required")
            if to_date < from_date or (to_date - from_date).days + 1 > 92:
                raise HTTPException(422, "invalid date range")
            collection = "employees"
            pipeline = trend_pipeline(department, from_date, to_date)
        operation = {"aggregate": collection, "pipeline": pipeline, "cursor": {}}
    result = db.command("explain", operation, verbosity="executionStats")
    return {"endpoint": endpoint, "collection": collection, "explain": result}
