from datetime import date, datetime, time
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class UserRegister(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(min_length=1, max_length=160)
    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.rsplit("@", 1)[-1]: raise ValueError("Enter a valid email address")
        return v

class LoginRequest(BaseModel):
    username: str
    password: str

class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(min_length=1, max_length=160)
    role: str = Field(default="EMPLOYEE", pattern="^(EMPLOYEE|HR|ADMIN)$")
    designation: str = "Employee"
    department: str = "Unassigned"
    joining_date: date = Field(default_factory=date.today)
    salary: float = Field(default=0, ge=0)

class UserUpdate(BaseModel):
    role: str | None = Field(default=None, pattern="^(EMPLOYEE|HR|ADMIN)$")
    is_active: bool | None = None
    email: str | None = None

class UserOut(BaseModel):
    id: int; username: str; email: str; role: str; is_active: bool; created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class EmployeeUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    designation: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    joining_date: date | None = None
    salary: float | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")

class EmployeeOut(BaseModel):
    id: int; user_id: int; full_name: str; designation: str; department: str; joining_date: date
    salary: float; status: str; username: str; email: str; role: str | None = None

class LeaveCreate(BaseModel):
    leave_type: str = Field(pattern="^(SICK|CASUAL|PAID)$")
    start_date: date; end_date: date; reason: str = Field(min_length=1, max_length=2000)
    @model_validator(mode="after")
    def dates_ordered(self):
        if self.end_date < self.start_date: raise ValueError("End date must be on or after start date")
        return self

class LeaveDecision(BaseModel):
    status: str = Field(pattern="^(APPROVED|REJECTED)$")

class LeavePolicyInput(BaseModel):
    leave_type: str = Field(pattern="^(SICK|CASUAL|PAID)$")
    total_days_allowed: int = Field(ge=0, le=365)

class LeaveBalanceInput(BaseModel):
    employee_id: int = Field(gt=0)
    leave_type: str = Field(pattern="^(SICK|CASUAL|PAID)$")
    remaining_days: int = Field(ge=0, le=365)

class PayrollCreate(BaseModel):
    employee_id: int = Field(gt=0)
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    basic_salary: float = Field(ge=0)
    allowances: float = Field(default=0, ge=0)
    deductions: float = Field(default=0, ge=0)
    tax_deduction: float = Field(default=0, ge=0)
    bonus: float = Field(default=0, ge=0)
    @model_validator(mode="after")
    def validate_net(self):
        if self.deductions + self.tax_deduction > self.basic_salary + self.allowances + self.bonus:
            raise ValueError("Deductions and tax cannot exceed gross earnings")
        return self

class ShiftInput(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    start_time: time; end_time: time
    grace_minutes: int = Field(default=10, ge=0, le=240)
    expected_hours: float = Field(default=8, gt=0, le=24)

class ShiftAssignmentInput(BaseModel):
    employee_id: int = Field(gt=0); shift_id: int = Field(gt=0)
    effective_from: date = Field(default_factory=date.today); effective_to: date | None = None
    @model_validator(mode="after")
    def dates_ordered(self):
        if self.effective_to and self.effective_to < self.effective_from: raise ValueError("effective_to must be on or after effective_from")
        return self

class PerformanceInput(BaseModel):
    employee_id: int = Field(gt=0)
    review_period: str = Field(min_length=2, max_length=40)
    rating: float = Field(ge=1, le=5)
    feedback: str = Field(min_length=1, max_length=5000)

class AttendanceOut(BaseModel):
    id: int; employee_id: int; check_in: datetime | None; check_out: datetime | None
    date: date; status: str; late_minutes: int = 0; early_checkin_minutes: int = 0; early_checkout_minutes: int = 0; overtime_hours: float = 0
    worked_hours: float | None = None; employee_name: str | None = None

class LeaveOut(BaseModel):
    id: int; employee_id: int; leave_type: str; start_date: date; end_date: date; reason: str
    status: str; created_at: datetime; employee_name: str | None = None; reviewed_by: int | None = None

class PayrollOut(BaseModel):
    id: int; employee_id: int; employee_name: str; annual_salary: float; monthly_salary: float
    basic_salary: float; allowances: float; deductions: float; tax_deduction: float; bonus: float
    net_salary: float; month: str; created_at: datetime

class NotificationOut(BaseModel):
    id: int; user_id: int; title: str; message: str; is_read: bool; created_at: datetime
    model_config = ConfigDict(from_attributes=True)
