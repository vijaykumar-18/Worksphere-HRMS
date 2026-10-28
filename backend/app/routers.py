from datetime import date, datetime, time, timedelta, timezone
import base64
from email.message import EmailMessage
import hashlib
import html
import json
import logging
import smtplib
from pathlib import Path
from uuid import uuid4
import csv
import io
import calendar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote, urlsplit
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, status
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import or_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user, get_employee_for_user, require_roles
from app.models import (Announcement, Attendance, Document, Employee, EmployeeShift, Feedback,
                        GoogleCalendarConnection, GoogleCalendarEvent, GoogleOAuthState, InternalTask,
                        Leave, LeaveBalance, LeavePolicy, Notification, OnboardingTask, Payroll,
                        PerformanceReview, Resignation, Shift, User, UserSettings, utc_now)
from app.schemas import (AttendanceOut, EmployeeOut, EmployeeUpdate, LeaveCreate, LeaveDecision, LeaveOut,
                         LeavePolicyInput, LeaveBalanceInput, PayrollCreate, PayrollOut, LoginRequest,
                         NotificationOut, PerformanceInput, ShiftInput, ShiftAssignmentInput, UserCreate,
                         UserOut, UserRegister, UserUpdate, AnnouncementCreate, FeedbackCreate,
                         InternalTaskCreate, InternalTaskUpdate, OnboardingTaskCreate, OnboardingTaskUpdate,
                         PasswordChange, ProfileUpdate, ResignationCreate, ResignationDecision,
                         UserSettingsUpdate)
from app.security import create_access_token, hash_password, verify_password

api = APIRouter()
logger = logging.getLogger(__name__)
UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploads"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx", ".txt"}
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"


def send_email(recipient: str, subject: str, message: str) -> None:
    if not settings.smtp_host or not settings.email_from:
        raise HTTPException(503, "Email is not configured. Set SMTP_HOST and EMAIL_FROM in backend .env.")
    email = EmailMessage()
    email["Subject"] = subject
    email["From"] = settings.email_from
    email["To"] = recipient
    email.set_content(message)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.starttls()
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(email)


def _google_configured() -> bool:
    return bool(settings.google_oauth_client_id and settings.google_oauth_client_secret)


def _google_token_cipher() -> Fernet:
    key_material = hashlib.sha256(
        f"worksphere-google-refresh-token:{settings.secret_key}".encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(key_material))


def _google_json_request(url: str, method: str = "GET", token: str | None = None,
                         payload: dict | None = None) -> dict:
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            content = response.read()
            return json.loads(content) if content else {}
    except HTTPError as exc:
        if exc.code == 404:
            return {"_http_status": 404}
        if exc.code == 401:
            raise HTTPException(400, "Google authorization expired or was revoked. Reconnect your calendar.") from exc
        logger.warning("Google API request failed with status %s", exc.code)
        raise HTTPException(502, "Google Calendar request failed. Check the connection and try again.") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("Google API request could not be completed: %s", type(exc).__name__)
        raise HTTPException(502, "Could not reach Google Calendar. Try again later.") from exc


def _google_access_token(connection: GoogleCalendarConnection) -> str:
    try:
        refresh_token = _google_token_cipher().decrypt(
            connection.encrypted_refresh_token.encode("ascii")
        ).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        logger.exception("Stored Google refresh token could not be decrypted for user_id=%s", connection.user_id)
        raise HTTPException(500, "Stored Google Calendar credentials cannot be read. Disconnect and reconnect the account.") from exc
    try:
        request = Request(
            GOOGLE_TOKEN_URL,
            data=urlencode({
                "client_id": settings.google_oauth_client_id or "",
                "client_secret": settings.google_oauth_client_secret or "",
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read())
    except HTTPError as exc:
        if exc.code in (400, 401):
            raise HTTPException(400, "Google authorization expired or was revoked. Reconnect your calendar.") from exc
        logger.warning("Google token refresh failed with status %s", exc.code)
        raise HTTPException(502, "Google token refresh failed. Try again later.") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("Google token refresh could not be completed: %s", type(exc).__name__)
        raise HTTPException(502, "Could not reach Google authentication. Try again later.") from exc
    if not isinstance(result, dict):
        raise HTTPException(502, "Google returned an invalid token refresh response.")
    access_token = result.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise HTTPException(502, "Google returned an invalid access token response.")
    return access_token


def google_oauth_result_page(result: str, message: str) -> HTMLResponse:
    target_origin = json.dumps(settings.frontend_origin.rstrip("/"))
    result_json = json.dumps(result)
    message_json = json.dumps(message)
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>Google Calendar</title>
<body><p>{html.escape(message)}</p>
<script>if(window.opener){{window.opener.postMessage({{type:"worksphere-google-calendar",status:{result_json},message:{message_json}}},{target_origin});window.close();}}</script></body></html>"""
    return HTMLResponse(page)


def notify(db: Session, user_id: int, title: str, message: str, email_preference: str | None = None) -> None:
    db.add(Notification(user_id=user_id, title=title, message=message))
    account = db.get(User, user_id)
    preferences = db.query(UserSettings).filter_by(user_id=user_id).first()
    preference_enabled = preferences and (getattr(preferences, email_preference) if email_preference else True)
    if (account and account.is_active and account.email and settings.smtp_host and settings.email_from
            and preferences and preferences.email_notifications and preference_enabled):
        try:
            send_email(account.email, title, message)
        except (HTTPException, OSError, smtplib.SMTPException):
            logger.exception("Could not send notification email to user_id=%s", user_id)


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
            db.query(OnboardingTask).filter_by(employee_id=employee.id).first(),
            db.query(Resignation).filter_by(employee_id=employee.id).first(),
            db.query(InternalTask).filter_by(assigned_to=employee.id).first(),
            db.query(Feedback).filter_by(employee_id=employee.id).first(),
        ])
        if has_history:
            raise HTTPException(409, "This user has HR history. Deactivate the account instead of permanently deleting it.")
    referenced = any([
        db.query(Leave).filter_by(reviewed_by=user_id).first(),
        db.query(Document).filter_by(uploaded_by=user_id).first(),
        db.query(PerformanceReview).filter_by(reviewer_id=user_id).first(),
        db.query(Announcement).filter_by(created_by=user_id).first(),
        db.query(InternalTask).filter_by(assigned_by=user_id).first(),
        db.query(Resignation).filter_by(reviewed_by=user_id).first(),
        db.query(GoogleCalendarConnection).filter_by(user_id=user_id).first(),
    ])
    if referenced: raise HTTPException(409, "This user is referenced by HR records. Deactivate the account instead.")
    db.query(Notification).filter_by(user_id=user_id).delete()
    db.query(UserSettings).filter_by(user_id=user_id).delete()
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


@api.patch("/employees/me")
def update_my_profile(payload: ProfileUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    employee = get_employee_for_user(user, db)
    existing = db.query(User).filter(User.email == payload.email, User.id != user.id).first()
    if existing:
        raise HTTPException(409, "Email address is already in use")
    employee.full_name = payload.full_name.strip()
    user.email = payload.email
    db.commit()
    db.refresh(employee)
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


# ONBOARDING
def onboarding_output(db: Session, item: OnboardingTask) -> dict:
    employee = db.get(Employee, item.employee_id)
    return {"id": item.id, "employee_id": item.employee_id,
            "employee_name": employee.full_name if employee else "Unknown",
            "task_name": item.task_name, "status": item.status, "due_date": item.due_date}


@api.get("/onboarding")
def list_onboarding(employee_id: int | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(OnboardingTask)
    if user.role not in ("HR", "ADMIN"):
        query = query.filter(OnboardingTask.employee_id == get_employee_for_user(user, db).id)
    elif employee_id:
        query = query.filter(OnboardingTask.employee_id == employee_id)
    return [onboarding_output(db, item) for item in query.order_by(OnboardingTask.due_date, OnboardingTask.id).limit(1000).all()]


@api.post("/onboarding", status_code=201)
def create_onboarding_task(payload: OnboardingTaskCreate, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    employee = get_employee(db, payload.employee_id)
    item = OnboardingTask(**payload.model_dump())
    db.add(item)
    db.flush()
    notify(db, employee.user_id, "New onboarding task assigned",
           f"{item.task_name} is due {item.due_date}.", "task_reminders")
    db.commit()
    db.refresh(item)
    return onboarding_output(db, item)


@api.patch("/onboarding/{task_id}")
def update_onboarding_task(task_id: int, payload: OnboardingTaskUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.get(OnboardingTask, task_id)
    if not item:
        raise HTTPException(404, "Onboarding task not found")
    if user.role not in ("HR", "ADMIN") and item.employee_id != get_employee_for_user(user, db).id:
        raise HTTPException(403, "You can only update your own onboarding checklist")
    item.status = payload.status
    db.commit()
    db.refresh(item)
    return onboarding_output(db, item)


# RESIGNATIONS
def resignation_output(db: Session, item: Resignation) -> dict:
    employee = db.get(Employee, item.employee_id)
    return {"id": item.id, "employee_id": item.employee_id,
            "employee_name": employee.full_name if employee else "Unknown",
            "reason": item.reason, "notice_period": item.notice_period, "status": item.status,
            "created_at": item.created_at, "reviewed_by": item.reviewed_by}


@api.post("/resignations", status_code=201)
def create_resignation(payload: ResignationCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    employee = get_employee_for_user(user, db)
    pending = db.query(Resignation).filter_by(employee_id=employee.id, status="PENDING").first()
    if pending:
        raise HTTPException(409, "You already have a pending resignation")
    item = Resignation(employee_id=employee.id, **payload.model_dump())
    db.add(item)
    db.flush()
    for recipient in db.query(User).filter(User.role.in_(["HR", "ADMIN"]), User.is_active.is_(True)).all():
        notify(db, recipient.id, "New resignation submitted", f"{employee.full_name} submitted a resignation request.")
    db.commit()
    db.refresh(item)
    return resignation_output(db, item)


@api.get("/resignations")
def list_resignations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(Resignation)
    if user.role not in ("HR", "ADMIN"):
        query = query.filter(Resignation.employee_id == get_employee_for_user(user, db).id)
    return [resignation_output(db, item) for item in query.order_by(Resignation.created_at.desc()).limit(1000).all()]


@api.patch("/resignations/{resignation_id}/decision")
def decide_resignation(resignation_id: int, payload: ResignationDecision, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    item = db.get(Resignation, resignation_id)
    if not item:
        raise HTTPException(404, "Resignation not found")
    if item.status != "PENDING":
        raise HTTPException(409, "This resignation has already been reviewed")
    item.status = payload.status
    item.reviewed_by = user.id
    employee = get_employee(db, item.employee_id)
    notify(db, employee.user_id, f"Resignation {payload.status.lower()}", f"Your resignation request was {payload.status.lower()}.")
    db.commit()
    db.refresh(item)
    return resignation_output(db, item)


# ANNOUNCEMENTS
def announcement_output(db: Session, item: Announcement) -> dict:
    creator = db.get(User, item.created_by)
    return {"id": item.id, "title": item.title, "message": item.message,
            "created_by": item.created_by, "created_by_name": creator.username if creator else "Unknown",
            "created_at": item.created_at}


@api.get("/announcements")
def list_announcements(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Announcement).order_by(Announcement.created_at.desc()).limit(500).all()
    return [announcement_output(db, item) for item in rows]


@api.get("/announcements/{announcement_id}")
def get_announcement(announcement_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.get(Announcement, announcement_id)
    if not item:
        raise HTTPException(404, "Announcement not found")
    return announcement_output(db, item)


@api.post("/announcements", status_code=201)
def create_announcement(payload: AnnouncementCreate, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    item = Announcement(created_by=user.id, **payload.model_dump())
    db.add(item)
    db.flush()
    for recipient in db.query(User).filter(User.is_active.is_(True), User.id != user.id).all():
        preferences = db.query(UserSettings).filter_by(user_id=recipient.id).first()
        if not preferences or preferences.announcement_notifications:
            notify(db, recipient.id, f"Announcement: {item.title}", item.message)
    db.commit()
    db.refresh(item)
    return announcement_output(db, item)


# INTERNAL TASKS
def task_output(db: Session, item: InternalTask) -> dict:
    employee = db.get(Employee, item.assigned_to)
    return {"id": item.id, "assigned_to": item.assigned_to,
            "employee_name": employee.full_name if employee else "Unknown",
            "title": item.title, "description": item.description, "due_date": item.due_date,
            "status": item.status, "assigned_by": item.assigned_by}


@api.get("/tasks")
def list_internal_tasks(employee_id: int | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(InternalTask)
    if user.role not in ("HR", "ADMIN"):
        query = query.filter(InternalTask.assigned_to == get_employee_for_user(user, db).id)
    elif employee_id:
        query = query.filter(InternalTask.assigned_to == employee_id)
    return [task_output(db, item) for item in query.order_by(InternalTask.due_date, InternalTask.id).all()]


@api.post("/tasks", status_code=201)
def create_internal_task(payload: InternalTaskCreate, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    employee = get_employee(db, payload.assigned_to)
    item = InternalTask(assigned_by=user.id, **payload.model_dump())
    db.add(item)
    db.flush()
    notify(db, employee.user_id, "New task assigned", f"{item.title} is due {item.due_date}.", "task_reminders")
    db.commit()
    db.refresh(item)
    return task_output(db, item)


@api.patch("/tasks/{task_id}")
def update_internal_task(task_id: int, payload: InternalTaskUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.get(InternalTask, task_id)
    if not item:
        raise HTTPException(404, "Task not found")
    if user.role not in ("HR", "ADMIN") and item.assigned_to != get_employee_for_user(user, db).id:
        raise HTTPException(403, "You can only update your own tasks")
    item.status = payload.status
    db.commit()
    db.refresh(item)
    return task_output(db, item)


# EMPLOYEE FEEDBACK
@api.post("/feedback", status_code=201)
def submit_feedback(payload: FeedbackCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    employee = get_employee_for_user(user, db)
    item = Feedback(employee_id=employee.id, **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@api.get("/feedback")
def list_feedback(employee_id: int | None = None, user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    query = db.query(Feedback)
    if employee_id:
        query = query.filter(Feedback.employee_id == employee_id)
    rows = query.order_by(Feedback.created_at.desc()).limit(1000).all()
    return [{"id": item.id, "employee_id": item.employee_id,
             "employee_name": (db.get(Employee, item.employee_id).full_name if db.get(Employee, item.employee_id) else "Unknown"),
             "message": item.message, "rating": item.rating, "created_at": item.created_at} for item in rows]


# PROFILE, PASSWORD AND PREFERENCES
@api.post("/auth/change-password")
def change_password(payload: PasswordChange, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(400, "Current password is incorrect")
    if verify_password(payload.new_password, user.hashed_password):
        raise HTTPException(400, "New password must be different from the current password")
    user.hashed_password = hash_password(payload.new_password)
    db.commit()
    return {"message": "Password changed successfully"}


def settings_output(item: UserSettings | None, user_id: int) -> dict:
    return {"user_id": user_id, "email_notifications": item.email_notifications if item else False,
            "task_reminders": item.task_reminders if item else True,
            "announcement_notifications": item.announcement_notifications if item else True}


@api.get("/settings/me")
def get_my_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(UserSettings).filter_by(user_id=user.id).first()
    return settings_output(item, user.id)


@api.put("/settings/me")
def update_my_settings(payload: UserSettingsUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(UserSettings).filter_by(user_id=user.id).first()
    if item is None:
        item = UserSettings(user_id=user.id, **payload.model_dump())
        db.add(item)
    else:
        for key, value in payload.model_dump().items():
            setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return settings_output(item, user.id)


# EMAIL AND GOOGLE CALENDAR INTEGRATIONS
@api.get("/integrations/status")
def integrations_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    connection = db.query(GoogleCalendarConnection).filter_by(user_id=user.id).first()
    callback = urlsplit(settings.google_calendar_redirect_uri)
    return {
        "email_configured": bool(settings.smtp_host and settings.email_from),
        "google_calendar_configured": _google_configured(),
        "google_calendar_connected": connection is not None,
        "google_calendar_connected_at": connection.connected_at if connection else None,
        "google_calendar_callback_origin": f"{callback.scheme}://{callback.netloc}",
    }


@api.post("/integrations/email/test")
def send_test_email(user: User = Depends(get_current_user)):
    try:
        send_email(user.email, "WorkSphere HRMS email test", "Your email notification settings are working.")
    except HTTPException:
        raise
    except (OSError, smtplib.SMTPException) as exc:
        logger.exception("SMTP test failed for user_id=%s", user.id)
        raise HTTPException(502, "Email delivery failed. Check SMTP settings and server logs.") from exc
    return {"message": f"Test email sent to {user.email}"}


@api.get("/integrations/google/authorize")
def authorize_google_calendar(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not _google_configured():
        raise HTTPException(503, "Google Calendar is not configured. Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in backend .env.")
    db.query(GoogleOAuthState).filter(GoogleOAuthState.expires_at < utc_now()).delete()
    state = uuid4().hex + uuid4().hex
    db.add(GoogleOAuthState(
        state=state,
        user_id=user.id,
        expires_at=utc_now() + timedelta(minutes=10),
    ))
    db.commit()
    authorization_url = GOOGLE_AUTH_URL + "?" + urlencode({
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_calendar_redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_CALENDAR_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    })
    return {"authorization_url": authorization_url}


@api.get("/integrations/google/callback", response_class=HTMLResponse)
def google_calendar_callback(state: str | None = None, code: str | None = None,
                             error: str | None = None, db: Session = Depends(get_db)):
    if not state:
        raise HTTPException(400, "Missing Google OAuth state.")
    oauth_state = db.get(GoogleOAuthState, state)
    if not oauth_state:
        raise HTTPException(400, "Google OAuth state is invalid or has already been used.")
    expiry = oauth_state.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry < utc_now():
        db.delete(oauth_state)
        db.commit()
        raise HTTPException(400, "Google OAuth state expired. Start the connection again.")
    user_id = oauth_state.user_id
    db.delete(oauth_state)
    db.commit()
    if error:
        return google_oauth_result_page("error", "Google Calendar authorization was not granted.")
    if not code:
        return google_oauth_result_page("error", "Google did not return an authorization code.")
    if not _google_configured():
        raise HTTPException(503, "Google Calendar is not configured on this server.")
    try:
        token_request = Request(
            GOOGLE_TOKEN_URL,
            data=urlencode({
                "code": code,
                "client_id": settings.google_oauth_client_id or "",
                "client_secret": settings.google_oauth_client_secret or "",
                "redirect_uri": settings.google_calendar_redirect_uri,
                "grant_type": "authorization_code",
            }).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(token_request, timeout=20) as response:
            token_result = json.loads(response.read())
    except HTTPError as exc:
        logger.warning("Google OAuth code exchange failed with status %s", exc.code)
        raise HTTPException(502, "Google authorization could not be completed. Check OAuth settings and try again.") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("Google OAuth code exchange could not be completed: %s", type(exc).__name__)
        raise HTTPException(502, "Could not reach Google authentication. Try again later.") from exc
    if not isinstance(token_result, dict):
        raise HTTPException(502, "Google returned an invalid authorization response.")
    refresh_token = token_result.get("refresh_token")
    if refresh_token is not None and (not isinstance(refresh_token, str) or not refresh_token):
        raise HTTPException(502, "Google returned an invalid refresh token.")
    connection = db.query(GoogleCalendarConnection).filter_by(user_id=user_id).first()
    if not refresh_token and not connection:
        raise HTTPException(400, "Google did not provide offline access. Remove this app from your Google Account permissions and connect again.")
    if connection is None:
        connection = GoogleCalendarConnection(
            user_id=user_id,
            encrypted_refresh_token=_google_token_cipher().encrypt(refresh_token.encode("utf-8")).decode("ascii"),
        )
        db.add(connection)
    elif refresh_token:
        connection.encrypted_refresh_token = _google_token_cipher().encrypt(refresh_token.encode("utf-8")).decode("ascii")
    connection.connected_at = utc_now()
    db.commit()
    return google_oauth_result_page("connected", "Google Calendar connected. You can close this window.")


@api.get("/integrations/google/status")
def google_calendar_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    connection = db.query(GoogleCalendarConnection).filter_by(user_id=user.id).first()
    return {"configured": _google_configured(), "connected": connection is not None,
            "connected_at": connection.connected_at if connection else None}


@api.delete("/integrations/google")
def disconnect_google_calendar(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    connection = db.query(GoogleCalendarConnection).filter_by(user_id=user.id).first()
    if connection:
        try:
            refresh_token = _google_token_cipher().decrypt(
                connection.encrypted_refresh_token.encode("ascii")
            ).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            logger.exception("Stored Google refresh token could not be decrypted for user_id=%s", user.id)
            raise HTTPException(500, "Stored Google Calendar credentials cannot be read. Contact an administrator.") from exc
        try:
            revoke_request = Request(
                "https://oauth2.googleapis.com/revoke",
                data=urlencode({"token": refresh_token}).encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urlopen(revoke_request, timeout=20):
                pass
        except HTTPError as exc:
            if exc.code != 400:
                logger.warning("Google token revocation failed with status %s", exc.code)
                raise HTTPException(502, "Google authorization could not be revoked. Try again later.") from exc
        except (URLError, TimeoutError) as exc:
            logger.warning("Google token revocation could not be completed: %s", type(exc).__name__)
            raise HTTPException(502, "Could not reach Google to revoke calendar authorization.") from exc
        db.query(GoogleCalendarEvent).filter_by(user_id=user.id).delete()
        db.delete(connection)
        db.commit()
    return {"message": "Google Calendar disconnected"}


@api.post("/integrations/google/sync")
def sync_google_calendar(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not _google_configured():
        raise HTTPException(503, "Google Calendar is not configured on this server.")
    connection = db.query(GoogleCalendarConnection).filter_by(user_id=user.id).first()
    if not connection:
        raise HTTPException(409, "Connect a Google Calendar account before syncing.")
    employee = get_employee_for_user(user, db)
    access_token = _google_access_token(connection)
    onboarding = db.query(OnboardingTask).filter_by(employee_id=employee.id).all()
    tasks = db.query(InternalTask).filter_by(assigned_to=employee.id).all()
    records = [
        (kind, item.id, item.task_name if kind == "onboarding" else item.title,
         item.due_date, item.status,
         "" if kind == "onboarding" else item.description)
        for kind, collection in (("onboarding", onboarding), ("task", tasks))
        for item in collection
    ]
    counts = {"created": 0, "updated": 0, "removed": 0}
    for kind, source_id, title, due_date, task_status, description in records:
        mapping = db.query(GoogleCalendarEvent).filter_by(
            user_id=user.id, source_type=kind, source_id=source_id,
        ).first()
        if task_status == "COMPLETED":
            if mapping:
                _google_json_request(
                    f"{GOOGLE_CALENDAR_API}/{quote(mapping.google_event_id, safe='')}",
                    method="DELETE", token=access_token,
                )
                db.delete(mapping)
                counts["removed"] += 1
            continue
        event = {
            "summary": title,
            "description": description,
            "start": {"date": due_date.isoformat()},
            "end": {"date": (due_date + timedelta(days=1)).isoformat()},
        }
        stale_mapping = None
        if mapping:
            url = f"{GOOGLE_CALENDAR_API}/{quote(mapping.google_event_id, safe='')}"
            result = _google_json_request(url, method="PATCH", token=access_token, payload=event)
            if result.get("_http_status") == 404:
                stale_mapping = mapping
                mapping = None
            else:
                counts["updated"] += 1
        if not mapping:
            result = _google_json_request(GOOGLE_CALENDAR_API, method="POST", token=access_token, payload=event)
            event_id = result.get("id")
            if not isinstance(event_id, str) or not event_id:
                raise HTTPException(502, "Google Calendar returned an invalid event response.")
            if mapping:
                mapping.google_event_id = event_id
            elif stale_mapping:
                stale_mapping.google_event_id = event_id
            else:
                db.add(GoogleCalendarEvent(
                    user_id=user.id, source_type=kind, source_id=source_id, google_event_id=event_id,
                ))
            counts["created"] += 1
    db.commit()
    return {"message": "Google Calendar sync complete", **counts}


# REPORT EXPORTS
@api.get("/exports/reports.csv")
def export_report(report_type: str = Query(pattern="^(employees|attendance|leaves|payroll|onboarding|resignations|tasks|feedback|announcements)$"),
                  start_date: date | None = None, end_date: date | None = None,
                  user: User = Depends(require_roles("HR", "ADMIN")), db: Session = Depends(get_db)):
    if start_date and end_date and end_date < start_date:
        raise HTTPException(400, "end_date must be on or after start_date")
    if report_type == "employees":
        rows = [employee_output(item) for item in db.query(Employee).order_by(Employee.id).all()]
    elif report_type == "attendance":
        query = db.query(Attendance)
        if start_date: query = query.filter(Attendance.date >= start_date)
        if end_date: query = query.filter(Attendance.date <= end_date)
        rows = [attendance_output(item, get_employee(db, item.employee_id).full_name) for item in query.order_by(Attendance.date).all()]
    elif report_type == "leaves":
        query = db.query(Leave)
        if start_date: query = query.filter(Leave.end_date >= start_date)
        if end_date: query = query.filter(Leave.start_date <= end_date)
        rows = [leave_output(db, item) for item in query.order_by(Leave.created_at).all()]
    elif report_type == "payroll":
        rows = [payroll_output(db, item) for item in db.query(Payroll).order_by(Payroll.month, Payroll.employee_id).all()]
    elif report_type == "onboarding":
        query = db.query(OnboardingTask)
        if start_date: query = query.filter(OnboardingTask.due_date >= start_date)
        if end_date: query = query.filter(OnboardingTask.due_date <= end_date)
        rows = [onboarding_output(db, item) for item in query.order_by(OnboardingTask.due_date).all()]
    elif report_type == "resignations":
        rows = [resignation_output(db, item) for item in db.query(Resignation).order_by(Resignation.created_at).all()]
    elif report_type == "tasks":
        rows = [task_output(db, item) for item in db.query(InternalTask).order_by(InternalTask.due_date).all()]
    elif report_type == "feedback":
        rows = [{"id": item.id, "employee_id": item.employee_id, "message": item.message, "rating": item.rating,
                 "created_at": item.created_at} for item in db.query(Feedback).order_by(Feedback.created_at).all()]
    else:
        rows = [announcement_output(db, item) for item in db.query(Announcement).order_by(Announcement.created_at).all()]
    output = io.StringIO(newline="")
    columns = list(rows[0].keys()) if rows else ["message"]
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    if rows:
        def safe_csv_value(value: object) -> object:
            if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
                return f"'{value}"
            return value
        writer.writerows({key: safe_csv_value(value) for key, value in row.items()} for row in rows)
    else:
        writer.writerow({"message": f"No {report_type} records found"})
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{report_type}-report.csv"'})
