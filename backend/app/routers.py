from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import csv
import io
import calendar

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import or_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user, get_employee_for_user, require_roles
from app.models import (Attendance, Document, Employee, EmployeeShift, Leave, LeaveBalance, LeavePolicy,
                        Notification, Payroll, PerformanceReview, Shift, User, utc_now)
from app.schemas import (AttendanceOut, EmployeeOut, EmployeeUpdate, LeaveCreate, LeaveDecision, LeaveOut,
                         LeavePolicyInput, LeaveBalanceInput, PayrollCreate, PayrollOut, LoginRequest,
                         NotificationOut, PerformanceInput, ShiftInput, ShiftAssignmentInput, UserCreate,
                         UserOut, UserRegister, UserUpdate)
from app.security import create_access_token, hash_password, verify_password

api = APIRouter()
UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploads"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx", ".txt"}


def notify(db: Session, user_id: int, title: str, message: str) -> None:
    db.add(Notification(user_id=user_id, title=title, message=message))


def employee_output(employee: Employee) -> dict:
    return {"id": employee.id, "user_id": employee.user_id, "full_name": employee.full_name,
            "designation": employee.designation, "department": employee.department,
            "joining_date": employee.joining_date, "salary": employee.salary, "status": employee.status,
            "username": employee.user.username, "email": employee.user.email, "role": employee.user.role}


def get_employee(db: Session, employee_id: int) -> Employee:
    employee = db.get(Employee, employee_id)
    if not employee: raise HTTPException(404, "Employee not found")
    return employee


def attendance_output(record: Attendance, employee_name: str | None = None) -> dict:
    hours = None
    if record.check_in and record.check_out:
        start, end = record.check_in, record.check_out
        if start.tzinfo is None: start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None: end = end.replace(tzinfo=timezone.utc)
        hours = round(max(0, (end - start).total_seconds() / 3600), 2)
    return {"id": record.id, "employee_id": record.employee_id, "check_in": record.check_in,
            "check_out": record.check_out, "date": record.date, "status": record.status,
            "late_minutes": record.late_minutes, "early_checkin_minutes": record.early_checkin_minutes,
            "early_checkout_minutes": record.early_checkout_minutes, "overtime_hours": record.overtime_hours,
            "worked_hours": hours, "employee_name": employee_name}


def leave_output(db: Session, item: Leave) -> dict:
    employee = db.get(Employee, item.employee_id)
    return {"id": item.id, "employee_id": item.employee_id, "leave_type": item.leave_type,
            "start_date": item.start_date, "end_date": item.end_date, "reason": item.reason,
            "status": item.status, "created_at": item.created_at, "employee_name": employee.full_name if employee else None,
            "reviewed_by": item.reviewed_by}


def payroll_output(db: Session, item: Payroll) -> dict:
    employee = db.get(Employee, item.employee_id)
    return {"id": item.id, "employee_id": item.employee_id, "employee_name": employee.full_name if employee else "Unknown",
            "annual_salary": item.annual_salary, "monthly_salary": item.monthly_salary, "basic_salary": item.basic_salary,
            "allowances": item.allowances, "deductions": item.deductions, "tax_deduction": item.tax_deduction,
            "bonus": item.bonus, "net_salary": item.net_salary, "month": item.month, "created_at": item.created_at}


def initialize_balances(db: Session, employee_id: int) -> None:
    policies = db.query(LeavePolicy).all()
    for policy in policies:
        exists = db.query(LeaveBalance).filter_by(employee_id=employee_id, leave_type=policy.leave_type).first()
        if not exists:
            db.add(LeaveBalance(employee_id=employee_id, leave_type=policy.leave_type, remaining_days=policy.total_days_allowed))


def leave_days(start: date, end: date) -> int:
    count = 0
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5: count += 1
        cursor += timedelta(days=1)
    return count


def active_shift(db: Session, employee_id: int, on_date: date) -> Shift | None:
    assignment = db.query(EmployeeShift).filter(EmployeeShift.employee_id == employee_id,
        EmployeeShift.effective_from <= on_date,
        or_(EmployeeShift.effective_to.is_(None), EmployeeShift.effective_to >= on_date)
    ).order_by(EmployeeShift.effective_from.desc()).first()
    return db.get(Shift, assignment.shift_id) if assignment else None


@api.post("/auth/register", response_model=UserOut, status_code=201)
def register(payload: UserRegister, db: Session = Depends(get_db)):
    if db.query(User).filter(or_(User.username == payload.username, User.email == payload.email)).first():
        raise HTTPException(409, "Username or email is already registered")
    account = User(username=payload.username, email=payload.email, hashed_password=hash_password(payload.password), role="EMPLOYEE")
    db.add(account); db.flush()
    employee = Employee(user_id=account.id, full_name=payload.full_name)
    db.add(employee); db.flush(); initialize_balances(db, employee.id)
    db.commit(); db.refresh(account)
    return account


@api.post("/auth/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    account = db.query(User).filter(User.username == payload.username).first()
    if account is None or not account.is_active or not verify_password(payload.password, account.hashed_password):
        raise HTTPException(401, "Incorrect username or password")
    return {"access_token": create_access_token(account.id, account.role), "token_type": "bearer", "user": UserOut.model_validate(account)}


@api.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)): return user


# ADMIN USER MANAGEMENT
@api.get("/admin/users", response_model=list[UserOut])
def list_users(q: str | None = None, user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    query = db.query(User)
    if q:
        term = f"%{q.strip()}%"; query = query.filter(or_(User.username.ilike(term), User.email.ilike(term)))
    return query.order_by(User.created_at.desc()).all()


@api.post("/admin/users", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, actor: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    if db.query(User).filter(or_(User.username == payload.username, User.email == payload.email.strip().lower())).first():
        raise HTTPException(409, "Username or email already exists")
    account = User(username=payload.username, email=payload.email.strip().lower(), hashed_password=hash_password(payload.password), role=payload.role)
    db.add(account); db.flush()
    employee = Employee(user_id=account.id, full_name=payload.full_name, designation=payload.designation,
        department=payload.department, joining_date=payload.joining_date, salary=payload.salary)
    db.add(employee); db.flush(); initialize_balances(db, employee.id)
    db.commit(); db.refresh(account)
    return account


@api.patch("/admin/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, actor: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    account = db.get(User, user_id)
    if not account: raise HTTPException(404, "User not found")
    data = payload.model_dump(exclude_unset=True)
    if user_id == actor.id and data.get("is_active") is False: raise HTTPException(400, "You cannot deactivate your own account")
    if user_id == actor.id and data.get("role") not in (None, "ADMIN"): raise HTTPException(400, "You cannot remove your own ADMIN role")
    if data.get("is_active") is False and account.role == "ADMIN" and account.is_active:
        active_admins = db.query(User).filter(User.role == "ADMIN", User.is_active.is_(True)).count()
        if active_admins <= 1: raise HTTPException(400, "Cannot deactivate the last active administrator")
    if data.get("role") and account.role == "ADMIN" and data["role"] != "ADMIN":
        active_admins = db.query(User).filter(User.role == "ADMIN", User.is_active.is_(True)).count()
        if account.is_active and active_admins <= 1: raise HTTPException(400, "Cannot demote the last active administrator")
    if "email" in data and data["email"]:
        data["email"] = data["email"].strip().lower()
        existing = db.query(User).filter(User.email == data["email"], User.id != user_id).first()
        if existing: raise HTTPException(409, "Email already exists")
    for key, value in data.items(): setattr(account, key, value)
    db.commit(); db.refresh(account)
    return account


@api.delete("/admin/users/{user_id}", status_code=204)
def delete_user(user_id: int, actor: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    account = db.get(User, user_id)
    if not account: raise HTTPException(404, "User not found")
    if account.id == actor.id: raise HTTPException(400, "You cannot delete your own account")
    if account.role == "ADMIN" and account.is_active and db.query(User).filter(User.role == "ADMIN", User.is_active.is_(True)).count() <= 1:
        raise HTTPException(400, "Cannot delete the last active administrator")
    employee = db.query(Employee).filter_by(user_id=user_id).first()
    if employee:
        has_history = any([
            db.query(Attendance).filter_by(employee_id=employee.id).first(),
            db.query(Leave).filter_by(employee_id=employee.id).first(),
            db.query(Payroll).filter_by(employee_id=employee.id).first(),
            db.query(EmployeeShift).filter_by(employee_id=employee.id).first(),
            db.query(Document).filter_by(employee_id=employee.id).first(),
            db.query(PerformanceReview).filter_by(employee_id=employee.id).first(),
        ])
        if has_history:
            raise HTTPException(409, "This user has HR history. Deactivate the account instead of permanently deleting it.")
    referenced = any([
        db.query(Leave).filter_by(reviewed_by=user_id).first(),
        db.query(Document).filter_by(uploaded_by=user_id).first(),
        db.query(PerformanceReview).filter_by(reviewer_id=user_id).first(),
    ])
    if referenced: raise HTTPException(409, "This user is referenced by HR records. Deactivate the account instead.")
    db.query(Notification).filter_by(user_id=user_id).delete()
    if employee:
        db.query(LeaveBalance).filter_by(employee_id=employee.id).delete()
        db.delete(employee)
    db.delete(account); db.commit()


# EMPLOYEE DIRECTORY / PROFILE
@api.get("/employees", response_model=list[EmployeeOut])
def list_employees(q: str | None = Query(None, max_length=120), department: str | None = None,
                   status_filter: str | None = Query(None, alias="status", pattern="^(ACTIVE|INACTIVE)$"),
                   role: str | None = Query(None, pattern="^(EMPLOYEE|HR|ADMIN)$"),
                   user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    query = db.query(Employee).join(User, Employee.user_id == User.id)
    if q:
        term = f"%{q.strip()}%"; query = query.filter(or_(Employee.full_name.ilike(term), Employee.department.ilike(term), Employee.designation.ilike(term), User.username.ilike(term), User.email.ilike(term)))
    if department: query = query.filter(Employee.department == department)
    if status_filter: query = query.filter(Employee.status == status_filter)
    if role: query = query.filter(User.role == role)
    return [employee_output(x) for x in query.order_by(Employee.full_name).all()]


@api.get("/employees/me", response_model=EmployeeOut)
def my_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    employee = get_employee_for_user(user, db)
    return employee_output(employee)


@api.get("/employees/{employee_id}", response_model=EmployeeOut)
def employee_detail(employee_id: int, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    employee = get_employee(db, employee_id)
    return employee_output(employee)


@api.post("/employees/onboard", response_model=EmployeeOut, status_code=201)
def onboard_employee(payload: UserCreate, actor: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    if db.query(User).filter(or_(User.username == payload.username, User.email == payload.email.strip().lower())).first():
        raise HTTPException(409, "Username or email is already registered")
    account = User(username=payload.username, email=payload.email.strip().lower(), hashed_password=hash_password(payload.password), role="EMPLOYEE")
    db.add(account); db.flush()
    employee = Employee(user_id=account.id, full_name=payload.full_name, designation=payload.designation, department=payload.department,
        joining_date=payload.joining_date, salary=payload.salary)
    db.add(employee); db.flush(); initialize_balances(db, employee.id)
    db.commit(); db.refresh(employee)
    return employee_output(employee)


@api.patch("/employees/{employee_id}", response_model=EmployeeOut)
def update_employee(employee_id: int, payload: EmployeeUpdate, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    employee = get_employee(db, employee_id)
    for key, value in payload.model_dump(exclude_unset=True).items(): setattr(employee, key, value)
    db.commit(); db.refresh(employee)
    return employee_output(employee)


# ATTENDANCE
@api.post("/attendance/check-in", response_model=AttendanceOut, status_code=201)
def check_in(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    employee = get_employee_for_user(user, db)
    if employee.status != "ACTIVE": raise HTTPException(403, "Inactive employees cannot mark attendance")
    now = datetime.now(timezone.utc); today = now.date()
    if db.query(Attendance).filter_by(employee_id=employee.id, date=today).first(): raise HTTPException(409, "Attendance has already been marked today")
    shift = active_shift(db, employee.id, today)
    late = 0; early = 0
    if shift:
        scheduled_start = datetime.combine(today, shift.start_time, tzinfo=timezone.utc)
        scheduled_with_grace = scheduled_start + timedelta(minutes=shift.grace_minutes)
        late = max(0, int((now - scheduled_with_grace).total_seconds() // 60))
        early = max(0, int((scheduled_start - now).total_seconds() // 60))
    record = Attendance(employee_id=employee.id, check_in=now, date=today, status="LATE" if late else "PRESENT", late_minutes=late, early_checkin_minutes=early)
    db.add(record)
    if late: notify(db, user.id, "Late attendance recorded", f"Your check-in was {late} minute(s) after your shift grace period.")
    db.commit(); db.refresh(record)
    return attendance_output(record, employee.full_name)


@api.post("/attendance/check-out", response_model=AttendanceOut)
def check_out(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    employee = get_employee_for_user(user, db); today = datetime.now(timezone.utc).date()
    record = db.query(Attendance).filter_by(employee_id=employee.id, date=today).first()
    if not record or not record.check_in: raise HTTPException(400, "Check in before checking out")
    if record.check_out: raise HTTPException(409, "You have already checked out today")
    record.check_out = datetime.now(timezone.utc)
    start = record.check_in
    end = record.check_out
    if start.tzinfo is None: start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None: end = end.replace(tzinfo=timezone.utc)
    hours = max(0, (end - start).total_seconds() / 3600)
    shift = active_shift(db, employee.id, today)
    expected = shift.expected_hours if shift else 8
    record.overtime_hours = round(max(0, hours - expected), 2)
    if shift:
        scheduled_end = datetime.combine(today, shift.end_time, tzinfo=timezone.utc)
        if shift.end_time <= shift.start_time: scheduled_end += timedelta(days=1)
        record.early_checkout_minutes = max(0, int((scheduled_end - end).total_seconds() // 60))
        if record.early_checkout_minutes > 0:
            notify(db, user.id, "Early check-out recorded", f"You checked out {record.early_checkout_minutes} minute(s) before your scheduled shift end.")
    db.commit(); db.refresh(record)
    return attendance_output(record, employee.full_name)


@api.get("/attendance", response_model=list[AttendanceOut])
def list_attendance(employee_id: int | None = None, start_date: date | None = None, end_date: date | None = None,
                    attendance_status: str | None = Query(None, alias="status"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(Attendance)
    if user.role not in ("HR", "ADMIN"):
        employee = get_employee_for_user(user, db); query = query.filter(Attendance.employee_id == employee.id)
    elif employee_id: query = query.filter(Attendance.employee_id == employee_id)
    if start_date: query = query.filter(Attendance.date >= start_date)
    if end_date: query = query.filter(Attendance.date <= end_date)
    if attendance_status: query = query.filter(Attendance.status == attendance_status)
    rows = query.order_by(Attendance.date.desc()).limit(1000).all()
    return [attendance_output(x, (db.get(Employee, x.employee_id).full_name if db.get(Employee, x.employee_id) else None)) for x in rows]


@api.post("/attendance/automation/run")
def run_attendance_automation(user: User = Depends(require_roles("ADMIN")), db: Session = Depends(get_db)):
    target = datetime.now(timezone.utc).date() - timedelta(days=1)
    employees = db.query(Employee).filter(Employee.status == "ACTIVE").all(); created = 0
    for employee in employees:
        shift = active_shift(db, employee.id, target)
        if not shift: continue
        if target.weekday() >= 5: continue
        if not db.query(Attendance).filter_by(employee_id=employee.id, date=target).first():
            db.add(Attendance(employee_id=employee.id, date=target, status="ABSENT")); notify(db, employee.user_id, "Attendance marked absent", f"No check-in was found for {target.isoformat()}."); created += 1
    db.commit()
    return {"date_processed": target.isoformat(), "absent_records_created": created}


# LEAVE POLICIES, BALANCES AND REQUESTS
@api.get("/leave-policies")
def list_leave_policies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(LeavePolicy).order_by(LeavePolicy.leave_type).all()


@api.put("/leave-policies")
def upsert_leave_policy(payload: LeavePolicyInput, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    policy = db.query(LeavePolicy).filter_by(leave_type=payload.leave_type).first()
    if not policy:
        policy = LeavePolicy(leave_type=payload.leave_type, total_days_allowed=payload.total_days_allowed); db.add(policy); db.flush()
    else: policy.total_days_allowed = payload.total_days_allowed
    for employee in db.query(Employee).all():
        balance = db.query(LeaveBalance).filter_by(employee_id=employee.id, leave_type=payload.leave_type).first()
        if not balance: db.add(LeaveBalance(employee_id=employee.id, leave_type=payload.leave_type, remaining_days=payload.total_days_allowed))
    db.commit(); db.refresh(policy)
    return policy


@api.get("/leave-balances")
def list_leave_balances(employee_id: int | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role not in ("HR", "ADMIN"):
        employee_id = get_employee_for_user(user, db).id
    query = db.query(LeaveBalance)
    if employee_id: query = query.filter(LeaveBalance.employee_id == employee_id)
    return query.order_by(LeaveBalance.employee_id, LeaveBalance.leave_type).all()


@api.put("/leave-balances")
def set_leave_balance(payload: LeaveBalanceInput, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    get_employee(db, payload.employee_id)
    balance = db.query(LeaveBalance).filter_by(employee_id=payload.employee_id, leave_type=payload.leave_type).first()
    if not balance:
        balance = LeaveBalance(**payload.model_dump()); db.add(balance)
    else: balance.remaining_days = payload.remaining_days
    db.commit(); db.refresh(balance); return balance


@api.post("/leaves", response_model=LeaveOut, status_code=201)
def create_leave(payload: LeaveCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    employee = get_employee_for_user(user, db)
    if payload.start_date < date.today(): raise HTTPException(400, "Leave cannot start in the past")
    overlap = db.query(Leave).filter(Leave.employee_id == employee.id, Leave.status.in_(["PENDING", "APPROVED"]),
        Leave.start_date <= payload.end_date, Leave.end_date >= payload.start_date).first()
    if overlap: raise HTTPException(409, "This leave overlaps an existing pending or approved request")
    initialize_balances(db, employee.id)
    balance = db.query(LeaveBalance).filter_by(employee_id=employee.id, leave_type=payload.leave_type).first()
    days = leave_days(payload.start_date, payload.end_date)
    if balance and days > balance.remaining_days: raise HTTPException(400, f"Insufficient {payload.leave_type} leave balance: {balance.remaining_days} day(s) remaining")
    item = Leave(employee_id=employee.id, **payload.model_dump()); db.add(item); db.flush()
    for hr_user in db.query(User).filter(User.role.in_(["HR", "ADMIN"]), User.is_active.is_(True)).all():
        notify(db, hr_user.id, "New leave request", f"{employee.full_name} requested {payload.leave_type} leave from {payload.start_date} to {payload.end_date}.")
    db.commit(); db.refresh(item); return leave_output(db, item)


@api.get("/leaves", response_model=list[LeaveOut])
def list_leaves(user: User = Depends(get_current_user), db: Session = Depends(get_db), status_filter: str | None = Query(None, alias="status", pattern="^(PENDING|APPROVED|REJECTED)$"), employee_id: int | None = None):
    query = db.query(Leave)
    if user.role not in ("HR", "ADMIN"): query = query.filter(Leave.employee_id == get_employee_for_user(user, db).id)
    elif employee_id: query = query.filter(Leave.employee_id == employee_id)
    if status_filter: query = query.filter(Leave.status == status_filter)
    return [leave_output(db, x) for x in query.order_by(Leave.created_at.desc()).limit(1000).all()]


@api.patch("/leaves/{leave_id}/decision", response_model=LeaveOut)
def decide_leave(leave_id: int, payload: LeaveDecision, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    item = db.get(Leave, leave_id)
    if not item: raise HTTPException(404, "Leave request not found")
    if item.status != "PENDING": raise HTTPException(409, "Only pending requests can be decided")
    employee = get_employee(db, item.employee_id)
    if payload.status == "APPROVED":
        initialize_balances(db, employee.id); balance = db.query(LeaveBalance).filter_by(employee_id=employee.id, leave_type=item.leave_type).first()
        days = leave_days(item.start_date, item.end_date)
        if balance and days > balance.remaining_days: raise HTTPException(400, f"Insufficient leave balance: {balance.remaining_days} day(s) remaining")
        if balance: balance.remaining_days -= days
    item.status = payload.status; item.reviewed_by = user.id
    notify(db, employee.user_id, f"Leave {payload.status.lower()}", f"Your {item.leave_type} leave request ({item.start_date} to {item.end_date}) was {payload.status.lower()} by HR.")
    db.commit(); db.refresh(item); return leave_output(db, item)


@api.get("/leave-calendar")
def leave_calendar(start_date: date = Query(default_factory=date.today), end_date: date | None = None, department: str | None = None,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    end_date = end_date or (start_date + timedelta(days=calendar.monthrange(start_date.year, start_date.month)[1] - 1))
    query = db.query(Leave).filter(Leave.status == "APPROVED", Leave.start_date <= end_date, Leave.end_date >= start_date)
    if user.role not in ("HR", "ADMIN"):
        query = query.filter(Leave.employee_id == get_employee_for_user(user, db).id)
    rows = []
    for item in query.all():
        employee = get_employee(db, item.employee_id)
        if department and employee.department != department: continue
        rows.append({**leave_output(db, item), "department": employee.department})
    return rows


# PAYROLL
@api.get("/payroll", response_model=list[PayrollOut])
def list_payroll(user: User = Depends(get_current_user), db: Session = Depends(get_db), employee_id: int | None = None, month: str | None = None):
    query = db.query(Payroll)
    if user.role not in ("HR", "ADMIN"): query = query.filter(Payroll.employee_id == get_employee_for_user(user, db).id)
    elif employee_id: query = query.filter(Payroll.employee_id == employee_id)
    if month: query = query.filter(Payroll.month == month)
    return [payroll_output(db, x) for x in query.order_by(Payroll.month.desc()).limit(1000).all()]


@api.post("/payroll", response_model=PayrollOut, status_code=201)
def create_payroll(payload: PayrollCreate, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    employee = get_employee(db, payload.employee_id)
    if db.query(Payroll).filter_by(employee_id=employee.id, month=payload.month).first(): raise HTTPException(409, "Payroll already exists for this employee and month")
    monthly = round(employee.salary / 12, 2)
    gross = payload.basic_salary + payload.allowances + payload.bonus
    net = round(gross - payload.deductions - payload.tax_deduction, 2)
    item = Payroll(employee_id=employee.id, annual_salary=employee.salary, monthly_salary=monthly,
        basic_salary=payload.basic_salary, allowances=payload.allowances, deductions=payload.deductions,
        tax_deduction=payload.tax_deduction, bonus=payload.bonus, net_salary=net, month=payload.month)
    db.add(item); db.flush(); notify(db, employee.user_id, "Payroll processed", f"Your {payload.month} payroll has been generated. Net pay: {net:.2f}.")
    db.commit(); db.refresh(item); return payroll_output(db, item)


@api.post("/payroll/generate-month")
def generate_month(month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    created = 0; skipped = 0
    for employee in db.query(Employee).filter(Employee.status == "ACTIVE").all():
        if db.query(Payroll).filter_by(employee_id=employee.id, month=month).first(): skipped += 1; continue
        monthly = round(employee.salary / 12, 2); basic = round(monthly * 0.5, 2); allowances = round(monthly - basic, 2)
        item = Payroll(employee_id=employee.id, annual_salary=employee.salary, monthly_salary=monthly,
            basic_salary=basic, allowances=allowances, deductions=0, tax_deduction=0, bonus=0, net_salary=monthly, month=month)
        db.add(item); notify(db, employee.user_id, "Payroll processed", f"Your {month} payroll has been generated. Net pay: {monthly:.2f}."); created += 1
    db.commit(); return {"month": month, "created": created, "skipped_existing": skipped, "rule": "annual salary / 12; 50% basic and 50% allowances; no tax/deductions inferred"}


@api.get("/payroll/{payroll_id}/payslip.csv")
def download_payslip(payroll_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.get(Payroll, payroll_id)
    if not item: raise HTTPException(404, "Payroll record not found")
    if user.role not in ("HR", "ADMIN") and get_employee_for_user(user, db).id != item.employee_id:
        raise HTTPException(403, "You cannot access this payslip")
    employee = get_employee(db, item.employee_id)
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["WorkSphere HRMS Payslip"]); writer.writerow(["Employee", employee.full_name]); writer.writerow(["Employee ID", employee.id]); writer.writerow(["Month", item.month])
    writer.writerow([]); writer.writerow(["Component", "Amount (INR)"])
    writer.writerow(["Basic salary", item.basic_salary]); writer.writerow(["Allowances", item.allowances]); writer.writerow(["Bonus", item.bonus])
    writer.writerow(["Deductions", item.deductions]); writer.writerow(["Tax deduction", item.tax_deduction]); writer.writerow(["Net salary", item.net_salary])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="payslip-{employee.id}-{item.month}.csv"'})


# NOTIFICATIONS
@api.get("/notifications", response_model=list[NotificationOut])
def list_notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Notification).filter(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(300).all()


@api.patch("/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_notification_read(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(Notification).filter_by(id=notification_id, user_id=user.id).first()
    if not item: raise HTTPException(404, "Notification not found")
    item.is_read = True; db.commit(); db.refresh(item); return item


@api.post("/notifications/read-all")
def mark_all_notifications_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read.is_(False)).update({"is_read": True})
    db.commit(); return {"message": "All notifications marked as read"}


# SHIFTS
@api.get("/shifts")
def list_shifts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Shift).order_by(Shift.start_time).all()


@api.post("/shifts", status_code=201)
def create_shift(payload: ShiftInput, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    if db.query(Shift).filter_by(name=payload.name).first(): raise HTTPException(409, "Shift name already exists")
    item = Shift(**payload.model_dump()); db.add(item); db.commit(); db.refresh(item); return item


@api.patch("/shifts/{shift_id}")
def update_shift(shift_id: int, payload: ShiftInput, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    item = db.get(Shift, shift_id)
    if not item: raise HTTPException(404, "Shift not found")
    for k, v in payload.model_dump().items(): setattr(item, k, v)
    db.commit(); db.refresh(item); return item


@api.get("/shift-assignments")
def list_shift_assignments(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(EmployeeShift)
    if user.role not in ("HR", "ADMIN"): query = query.filter(EmployeeShift.employee_id == get_employee_for_user(user, db).id)
    result = []
    for item in query.order_by(EmployeeShift.effective_from.desc()).all():
        emp, shift = db.get(Employee, item.employee_id), db.get(Shift, item.shift_id)
        result.append({"id": item.id, "employee_id": item.employee_id, "employee_name": emp.full_name if emp else "Unknown",
            "shift_id": item.shift_id, "shift_name": shift.name if shift else "Unknown", "start_time": shift.start_time if shift else None,
            "end_time": shift.end_time if shift else None, "effective_from": item.effective_from, "effective_to": item.effective_to})
    return result


@api.post("/shift-assignments", status_code=201)
def assign_shift(payload: ShiftAssignmentInput, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    get_employee(db, payload.employee_id)
    if not db.get(Shift, payload.shift_id): raise HTTPException(404, "Shift not found")
    # Close previous open assignment before adding the new one.
    for previous in db.query(EmployeeShift).filter(EmployeeShift.employee_id == payload.employee_id, EmployeeShift.effective_to.is_(None)).all():
        if previous.effective_from <= payload.effective_from: previous.effective_to = payload.effective_from - timedelta(days=1)
    item = EmployeeShift(**payload.model_dump()); db.add(item); db.commit(); db.refresh(item); return item


# DOCUMENT MANAGEMENT
@api.get("/documents")
def list_documents(employee_id: int | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role not in ("HR", "ADMIN"): employee_id = get_employee_for_user(user, db).id
    query = db.query(Document)
    if employee_id: query = query.filter(Document.employee_id == employee_id)
    return query.order_by(Document.uploaded_at.desc()).all()


@api.post("/documents", status_code=201)
def upload_document(employee_id: int = Form(...), expires_on: date | None = Form(None), file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    employee = get_employee(db, employee_id)
    if user.role not in ("HR", "ADMIN") and employee.user_id != user.id: raise HTTPException(403, "You can only upload documents to your own profile")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS: raise HTTPException(415, "Unsupported file type. Allowed: PDF, PNG, JPG, DOC, DOCX, TXT")
    contents = file.file.read(MAX_UPLOAD_BYTES + 1)
    if not contents: raise HTTPException(400, "Uploaded file is empty")
    if len(contents) > MAX_UPLOAD_BYTES: raise HTTPException(413, "File exceeds 10 MB limit")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}{ext}"
    (UPLOAD_DIR / stored_name).write_bytes(contents)
    item = Document(employee_id=employee_id, file_name=Path(file.filename or "document").name[:255], stored_name=stored_name,
                    content_type=file.content_type or "application/octet-stream", uploaded_by=user.id, expires_on=expires_on)
    db.add(item); db.flush()
    if expires_on:
        notify(db, employee.user_id, "Document expiry recorded", f"A document expires on {expires_on.isoformat()}.")
    db.commit(); db.refresh(item); return item


@api.get("/documents/{document_id}/download")
def download_document(document_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.get(Document, document_id)
    if not item: raise HTTPException(404, "Document not found")
    employee = get_employee(db, item.employee_id)
    if user.role not in ("HR", "ADMIN") and employee.user_id != user.id: raise HTTPException(403, "You cannot access this document")
    path = UPLOAD_DIR / item.stored_name
    if not path.is_file(): raise HTTPException(404, "Stored file is missing")
    return FileResponse(path, filename=item.file_name, media_type=item.content_type)


@api.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: int, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    item = db.get(Document, document_id)
    if not item: raise HTTPException(404, "Document not found")
    path = UPLOAD_DIR / item.stored_name
    if path.exists(): path.unlink()
    db.delete(item); db.commit()


# PERFORMANCE MANAGEMENT
@api.get("/performance")
def list_performance(employee_id: int | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(PerformanceReview)
    if user.role not in ("HR", "ADMIN"): query = query.filter(PerformanceReview.employee_id == get_employee_for_user(user, db).id)
    elif employee_id: query = query.filter(PerformanceReview.employee_id == employee_id)
    rows = query.order_by(PerformanceReview.created_at.desc()).all(); result = []
    for row in rows:
        emp = db.get(Employee, row.employee_id); reviewer = db.get(User, row.reviewer_id)
        result.append({"id": row.id, "employee_id": row.employee_id, "employee_name": emp.full_name if emp else "Unknown",
            "reviewer_id": row.reviewer_id, "reviewer_username": reviewer.username if reviewer else "Unknown",
            "review_period": row.review_period, "rating": row.rating, "feedback": row.feedback, "created_at": row.created_at})
    return result


@api.post("/performance", status_code=201)
def create_performance(payload: PerformanceInput, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    get_employee(db, payload.employee_id)
    item = PerformanceReview(reviewer_id=user.id, **payload.model_dump()); db.add(item); db.flush()
    employee = get_employee(db, payload.employee_id); notify(db, employee.user_id, "Performance review added", f"A review for {payload.review_period} is available in your portal.")
    db.commit(); db.refresh(item); return {"id": item.id, **payload.model_dump(), "reviewer_id": user.id, "created_at": item.created_at}


@api.delete("/performance/{review_id}", status_code=204)
def delete_performance(review_id: int, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    item = db.get(PerformanceReview, review_id)
    if not item: raise HTTPException(404, "Performance review not found")
    db.delete(item); db.commit()


# DASHBOARD / ANALYTICS / ADVANCED SEARCH
@api.get("/dashboard/summary")
def dashboard_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    today = datetime.now(timezone.utc).date()
    if user.role in ("HR", "ADMIN"):
        total_employees = db.query(Employee).filter(Employee.status == "ACTIVE").count()
        present_today = db.query(Attendance).filter(Attendance.date == today, Attendance.status.in_(["PRESENT", "LATE"])).count()
        absent_today = db.query(Attendance).filter(Attendance.date == today, Attendance.status == "ABSENT").count()
        pending_leaves = db.query(Leave).filter(Leave.status == "PENDING").count()
        payroll_month = today.strftime("%Y-%m")
        payroll_total = db.query(func.coalesce(func.sum(Payroll.net_salary), 0)).filter(Payroll.month == payroll_month).scalar()
        return {"role": user.role, "total_employees": total_employees, "present_today": present_today, "absent_today": absent_today,
            "pending_leaves": pending_leaves, "current_month_payroll": float(payroll_total or 0),
            "department_counts": [{"department": name or "Unassigned", "count": count} for name, count in db.query(Employee.department, func.count(Employee.id)).filter(Employee.status == "ACTIVE").group_by(Employee.department).all()]}
    employee = get_employee_for_user(user, db)
    return {"role": user.role, "total_employees": 1, "present_today": db.query(Attendance).filter(Attendance.employee_id == employee.id, Attendance.date == today, Attendance.status.in_(["PRESENT", "LATE"])).count(),
        "pending_leaves": db.query(Leave).filter_by(employee_id=employee.id, status="PENDING").count(),
        "current_month_payroll": float(db.query(func.coalesce(func.sum(Payroll.net_salary), 0)).filter(Payroll.employee_id == employee.id, Payroll.month == today.strftime("%Y-%m")).scalar() or 0)}


@api.get("/analytics/reports")
def analytics_reports(start_date: date | None = None, end_date: date | None = None, department: str | None = None,
                     user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    attendance_query = db.query(Attendance)
    leave_query = db.query(Leave)
    performance_query = db.query(PerformanceReview).join(Employee, PerformanceReview.employee_id == Employee.id)
    if start_date: attendance_query = attendance_query.filter(Attendance.date >= start_date); leave_query = leave_query.filter(Leave.end_date >= start_date)
    if end_date: attendance_query = attendance_query.filter(Attendance.date <= end_date); leave_query = leave_query.filter(Leave.start_date <= end_date)
    if department:
        attendance_query = attendance_query.join(Employee, Attendance.employee_id == Employee.id).filter(Employee.department == department)
        leave_query = leave_query.join(Employee, Leave.employee_id == Employee.id).filter(Employee.department == department)
        performance_query = performance_query.filter(Employee.department == department)
    attendance_rows = attendance_query.all(); leave_rows = leave_query.all(); reviews = performance_query.all()
    return {"attendance_by_status": {status_name: sum(1 for row in attendance_rows if row.status == status_name) for status_name in ["PRESENT", "LATE", "ABSENT", "HALF_DAY"]},
        "leave_by_status": {status_name: sum(1 for row in leave_rows if row.status == status_name) for status_name in ["PENDING", "APPROVED", "REJECTED"]},
        "leave_by_type": {kind: sum(1 for row in leave_rows if row.leave_type == kind) for kind in ["SICK", "CASUAL", "PAID"]},
        "average_performance_rating": round(sum(x.rating for x in reviews) / len(reviews), 2) if reviews else None,
        "performance_review_count": len(reviews), "attendance_records": len(attendance_rows), "leave_requests": len(leave_rows)}


@api.get("/employees/search/advanced")
def advanced_employee_search(q: str | None = None, department: str | None = None, role: str | None = Query(None, pattern="^(EMPLOYEE|HR|ADMIN)$"),
                            status_filter: str | None = Query(None, alias="status", pattern="^(ACTIVE|INACTIVE)$"),
                            attendance_status: str | None = Query(None, pattern="^(PRESENT|LATE|ABSENT|HALF_DAY|NOT_MARKED)$"),
                            min_rating: float | None = Query(None, ge=1, le=5),
                            user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    query = db.query(Employee).join(User, Employee.user_id == User.id)
    if q:
        term = f"%{q.strip()}%"; query = query.filter(or_(Employee.full_name.ilike(term), Employee.department.ilike(term), Employee.designation.ilike(term), User.username.ilike(term), User.email.ilike(term)))
    if department: query = query.filter(Employee.department == department)
    if role: query = query.filter(User.role == role)
    if status_filter: query = query.filter(Employee.status == status_filter)
    results = []
    for employee in query.order_by(Employee.full_name).all():
        latest = db.query(PerformanceReview).filter_by(employee_id=employee.id).order_by(PerformanceReview.created_at.desc()).first()
        today_record = db.query(Attendance).filter_by(employee_id=employee.id, date=datetime.now(timezone.utc).date()).first()
        today_status = today_record.status if today_record else "NOT_MARKED"
        if attendance_status and today_status != attendance_status: continue
        if min_rating is not None and (latest is None or latest.rating < min_rating): continue
        results.append({**employee_output(employee), "latest_rating": latest.rating if latest else None,
                        "today_attendance": today_status})
    return results
