from datetime import date, datetime, timezone, time
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="EMPLOYEE", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    employee: Mapped["Employee | None"] = relationship(back_populates="user", uselist=False)

class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    designation: Mapped[str] = mapped_column(String(120), default="Employee")
    department: Mapped[str] = mapped_column(String(120), default="Unassigned", index=True)
    joining_date: Mapped[date] = mapped_column(Date, default=date.today)
    salary: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    user: Mapped[User] = relationship(back_populates="employee")

class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("employee_id", "date", name="uq_attendance_employee_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    check_in: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    check_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), default="PRESENT")
    late_minutes: Mapped[int] = mapped_column(Integer, default=0)
    early_checkin_minutes: Mapped[int] = mapped_column(Integer, default=0)
    early_checkout_minutes: Mapped[int] = mapped_column(Integer, default=0)
    overtime_hours: Mapped[float] = mapped_column(Float, default=0)

class Leave(Base):
    __tablename__ = "leaves"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    leave_type: Mapped[str] = mapped_column(String(20), index=True)
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

class Payroll(Base):
    __tablename__ = "payroll"
    __table_args__ = (UniqueConstraint("employee_id", "month", name="uq_payroll_employee_month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    annual_salary: Mapped[float] = mapped_column(Float)
    monthly_salary: Mapped[float] = mapped_column(Float)
    basic_salary: Mapped[float] = mapped_column(Float)
    allowances: Mapped[float] = mapped_column(Float, default=0)
    deductions: Mapped[float] = mapped_column(Float, default=0)
    tax_deduction: Mapped[float] = mapped_column(Float, default=0)
    bonus: Mapped[float] = mapped_column(Float, default=0)
    net_salary: Mapped[float] = mapped_column(Float)
    month: Mapped[str] = mapped_column(String(7), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    message: Mapped[str] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

class LeavePolicy(Base):
    __tablename__ = "leave_policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    leave_type: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    total_days_allowed: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

class LeaveBalance(Base):
    __tablename__ = "leave_balances"
    __table_args__ = (UniqueConstraint("employee_id", "leave_type", name="uq_leave_balance_employee_type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    leave_type: Mapped[str] = mapped_column(String(20))
    remaining_days: Mapped[int] = mapped_column(Integer, default=0)

class Shift(Base):
    __tablename__ = "shifts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    grace_minutes: Mapped[int] = mapped_column(Integer, default=10)
    expected_hours: Mapped[float] = mapped_column(Float, default=8)

class EmployeeShift(Base):
    __tablename__ = "employee_shifts"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), index=True)
    effective_from: Mapped[date] = mapped_column(Date, default=date.today)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

class PerformanceReview(Base):
    __tablename__ = "performance"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    review_period: Mapped[str] = mapped_column(String(40), index=True)
    rating: Mapped[float] = mapped_column(Float)
    feedback: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
