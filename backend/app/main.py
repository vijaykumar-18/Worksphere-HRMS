import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import or_
from app.database import Base, engine, SessionLocal
from app import models
from app.models import Employee, Attendance, EmployeeShift, Shift, LeavePolicy, Notification, User, utc_now
from app.routers import api, active_shift


def seed_leave_policies() -> None:
    db = SessionLocal()
    try:
        defaults = {"SICK": 10, "CASUAL": 12, "PAID": 15}
        for leave_type, days in defaults.items():
            if not db.query(LeavePolicy).filter_by(leave_type=leave_type).first():
                db.add(LeavePolicy(leave_type=leave_type, total_days_allowed=days))
        db.commit()
        for employee in db.query(Employee).all():
            for policy in db.query(LeavePolicy).all():
                from app.models import LeaveBalance
                if not db.query(LeaveBalance).filter_by(employee_id=employee.id, leave_type=policy.leave_type).first():
                    db.add(LeaveBalance(employee_id=employee.id, leave_type=policy.leave_type, remaining_days=policy.total_days_allowed))
        db.commit()
    finally:
        db.close()


def process_absences(target_date) -> None:
    db = SessionLocal()
    try:
        if target_date.weekday() >= 5:
            return
        for employee in db.query(Employee).filter(Employee.status == "ACTIVE").all():
            assignment = db.query(EmployeeShift).filter(EmployeeShift.employee_id == employee.id,
                EmployeeShift.effective_from <= target_date,
                or_(EmployeeShift.effective_to.is_(None), EmployeeShift.effective_to >= target_date)
            ).order_by(EmployeeShift.effective_from.desc()).first()
            if not assignment:
                continue
            if not db.query(Attendance).filter_by(employee_id=employee.id, date=target_date).first():
                db.add(Attendance(employee_id=employee.id, date=target_date, status="ABSENT"))
                db.add(Notification(user_id=employee.user_id, title="Attendance marked absent",
                    message=f"No check-in was recorded for {target_date.isoformat()}.", is_read=False, created_at=utc_now()))
        # Expiry alert: notify employee 30 days before expiry; avoid duplicate alerts by matching today's title/message.
        from app.models import Document
        alert_date = target_date + timedelta(days=30)
        for doc in db.query(Document).filter(Document.expires_on == alert_date).all():
            employee = db.get(Employee, doc.employee_id)
            if not employee:
                continue
            message = f"Document '{doc.file_name}' expires on {alert_date.isoformat()} (30-day reminder)."
            exists = db.query(Notification).filter_by(user_id=employee.user_id, title="Document expiry reminder", message=message).first()
            if not exists:
                db.add(Notification(user_id=employee.user_id, title="Document expiry reminder", message=message, is_read=False, created_at=utc_now()))
        db.commit()
    finally:
        db.close()


async def daily_automation():
    last_processed = None
    while True:
        now = datetime.now(timezone.utc)
        if now.hour == 0 and now.minute >= 5 and last_processed != now.date():
            process_absences(now.date() - timedelta(days=1))
            last_processed = now.date()
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    seed_leave_policies()
    task = asyncio.create_task(daily_automation())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="WorkSphere HRMS API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(api, prefix="/api")

@app.get("/health")
def health():
    return {"status": "ok", "application": "WorkSphere HRMS"}
