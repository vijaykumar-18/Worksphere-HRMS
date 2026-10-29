# WorkSphere HRMS — Phases 1–5

A full-stack HR Management System built with **FastAPI, SQLAlchemy, SQLite, JWT, Pydantic Settings, pwdlib/Argon2, React, JSX and Vite**. The frontend source uses JSX; Vite and React handle the build.

> **Important:** This is a functional learning/reference implementation, not a certified production payroll or HR platform. Before real employee data is used, conduct a security review, choose the organization's time zone and HR policies, configure HTTPS/backups, and validate payroll/legal requirements. Automated absence processing currently uses UTC calendar dates, weekdays, and assigned shifts; configure it to match your organization's actual working calendar before relying on it.

## Included modules

### Phase 1 — Core HRMS
- Registration and login, JWT access tokens, Argon2 password hashing, current-user endpoint
- Employee directory, onboarding, profile, employee updates, search and filters
- Check-in/check-out, working-hours calculation, duplicate check-in protection, attendance history
- Leave requests, overlap checks, HR/Admin approval/rejection, leave history
- Payroll records, salary breakdown, monthly generation and downloadable CSV payslips
- HR dashboard KPIs, department distribution, analytics endpoints
- User-owned notifications, read/unread controls, leave/payroll/attendance/document/performance triggers, and frontend notification refresh polling every 30 seconds

### Phase 2 — RBAC and advanced HR
- Server-side role dependencies and ownership checks
- Employee self-service profile, attendance, leave, payroll, notifications and document views
- Leave policies and employee balances; balance validation and weekday-based deductions on approval
- Admin user management, role assignment and account activation/deactivation
- Late check-in calculation from assigned shift start time and grace period; overtime calculation at check-out

### Phase 3 — Automation and employee experience
- Shift creation and effective-dated employee shift assignments
- Daily UTC background automation for absent records on the previous weekday when a shift is assigned
- Manual admin-triggered absence automation for the previous day
- Approved leave calendar/team view and department filter
- Secure document upload/download/delete, file-type allowlist, 10 MB size limit, ownership checks and expiry reminder job
- Performance reviews, ratings and feedback history
- Analytics reports for attendance, leave and performance, with date/department filters
- Advanced employee search with role, status, attendance and latest performance fields

### Phase 4 — Employee experience and internal workflows (Modules 21–24, 26–27)
- Employee onboarding checklists, task assignment, completion tracking and progress
- Employee resignation requests and HR/Admin approval or rejection
- Internal announcement board and announcement detail view (HR/Admin can post)
- HR/Admin task assignment and employee-owned task completion tracking
- Employee feedback submission and HR/Admin feedback dashboard
- Employee profile and email updates, password changes and notification preferences

### Phase 5 — Integrations and exports (Module 25)
- SMTP email delivery with a test-email action; notifications follow employee email preferences
- Direct per-user Google Calendar OAuth connection, encrypted refresh-token storage, and idempotent due-date sync
- HR/Admin CSV exports for employees, attendance, leave, payroll, onboarding, resignations, tasks, feedback and announcements
- Print-friendly reports that can be saved as PDF from the browser

## Folder structure

```text
HRMS/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   ├── routers.py
│   │   ├── dependencies.py
│   │   ├── security.py
│   │   ├── database.py
│   │   ├── config.py
│   │   └── bootstrap_user.py
│   ├── tests/
│   ├── uploads/
│   ├── .env.example
│   └── requirements.txt
├── frontend/
│   ├── src/
│   ├── package.json
│   ├── package-lock.json
│   └── vite.config.ts
├── .gitignore
└── README.md
```

## Requirements
- Python 3.10 or newer (Python 3.11/3.12 recommended)
- Node.js 20 or newer and npm
- VS Code or another editor

Run the backend and frontend from separate PowerShell terminals using the commands below. The backend setup creates a local virtual environment; the frontend uses npm.

## Run on Windows PowerShell

### 1. Backend setup (once)

```powershell
cd C:\path\to\HRMS\backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Open `backend/.env` and replace `SECRET_KEY` with a long random value (at least 32 characters). Keep `.env` private and never commit it. SMTP and Google OAuth settings are optional; configure them below to enable those integrations.

If PowerShell blocks environment activation, run this once in the same terminal:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

### 2. Start backend

From `backend/` with `.venv` activated:

```powershell
python -m uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- API docs: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health

### 3. Create an HR or Admin account

Public self-registration intentionally creates only EMPLOYEE accounts. In a second backend terminal, activate the same virtual environment and run:

```powershell
python -m app.bootstrap_user --username admin --email admin@example.com --name "WorkSphere Admin" --role ADMIN
```

Enter a password of at least 10 characters twice when prompted. Use `--role HR` to create an HR account. This CLI is intended for trusted local administration; do not expose public role assignment at registration.

### 4. Frontend setup (once)

Open a second terminal:

```powershell
cd C:\path\to\HRMS\frontend
npm install
```

### 5. Start frontend

```powershell
npm run dev
```

Open the Vite URL printed in the terminal, normally http://localhost:5173.

## Configure email delivery

Email is sent by the backend using SMTP. For Gmail, enable 2-Step Verification and generate an App Password; use the App Password, not your Google account password. Put these values in `backend\.env` (do not commit or share the file):

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-address@gmail.com
SMTP_PASSWORD=your-16-character-app-password
EMAIL_FROM=your-address@gmail.com
```

Restart the backend, open **Profile & settings**, enable **Email notifications** (and task assignment emails if desired), then use **Integrations & exports → Send test email** to verify delivery. SMTP support requires a mail server that accepts STARTTLS on the configured port.

## Configure direct Google Calendar sync

1. In [Google Cloud Console](https://console.cloud.google.com/), select or create a project and enable **Google Calendar API**.
2. Configure the OAuth consent screen. For an app in Testing mode, add the Google account you will connect as a test user.
3. Create an OAuth client ID for a **Web application**. Add this exact authorized redirect URI:
   `http://127.0.0.1:8000/api/integrations/google/callback`
4. Put the client ID and secret in `backend\.env`:

   ```dotenv
   GOOGLE_OAUTH_CLIENT_ID=your-google-oauth-client-id
   GOOGLE_OAUTH_CLIENT_SECRET=your-google-oauth-client-secret
   GOOGLE_CALENDAR_REDIRECT_URI=http://127.0.0.1:8000/api/integrations/google/callback
   FRONTEND_ORIGIN=http://localhost:5173
   ```

5. Restart the backend. Sign in to WorkSphere using the account whose calendar should receive its own tasks, then open **Integrations & exports → Connect Google Calendar**. Allow the popup and approve calendar access. Use **Sync my tasks** to create or update that account's onboarding and assigned-task due dates; completed task events are removed on the next sync.

Google credentials and refresh tokens must remain private. Refresh tokens are encrypted in the database using `SECRET_KEY`; keep that key stable and backed up. If it is rotated, reconnect Google Calendar accounts. Each WorkSphere user connects their own Google account; this does not sync company-wide events into every employee's calendar.

## Database notes

- A fresh SQLite database (`worksphere.db`) is created when the API starts.
- Default leave policies are seeded if they do not exist: SICK 10 days, CASUAL 12 days, PAID 15 days. Adjust these through the Leave policies page to match your organization.
- Leave duration currently counts weekdays (Monday–Friday), not configured public holidays.
- Payroll generation uses annual salary / 12, split 50% basic and 50% allowances, with zero tax/deductions by default. These are demonstration defaults, not tax or legal payroll calculations.
- Automatic absence is applied only to active employees who have an assigned shift and have no attendance record for the previous weekday. The background job uses UTC. Review this behavior before real use.
- `Base.metadata.create_all()` creates missing tables but is not a schema migration tool. If you already have an older database, back it up and use a fresh database for this version, or add Alembic migrations before upgrading an existing database.
- Uploaded documents are saved under `backend/uploads/` using generated storage names. Treat this folder as private data and include it in secured backups only; do not commit uploads to Git.

## Tests

From `backend/` after installing requirements:

```powershell
pytest -q
```

The tests cover health, employee registration/login/profile, public role assignment protection, employee access restrictions, duplicate check-in protection, onboarding and exit workflows, announcements, task ownership, feedback, profile/settings/password actions, CSV report access, Google OAuth/sync lifecycle (mocked) and SMTP test delivery (mocked). Add more tests before production use, especially for admin operations, leave balance deductions, document access and payroll rules.

## Main API areas

All API routes are under `/api`:

- `/auth/*`
- `/admin/users*`
- `/employees*` and `/employees/search/advanced`
- `/attendance*`
- `/leaves*`, `/leave-policies`, `/leave-balances`, `/leave-calendar`
- `/payroll*` and `/payroll/{id}/payslip.csv`
- `/notifications*`
- `/shifts*`, `/shift-assignments*`
- `/documents*`
- `/performance*`
- `/onboarding*`, `/resignations*`, `/announcements*`, `/tasks*`, `/feedback*`
- `/settings/me`, `/auth/change-password`, `/exports/reports.csv`, `/integrations/*`
- `/dashboard/summary`, `/analytics/reports`

## New module usage

- Employees manage their own onboarding checklist, resignation request, assigned tasks, feedback, profile and notification preferences.
- HR/Admin users can assign onboarding and internal tasks, review resignations, publish announcements, inspect employee feedback and export reports.
- Email is disabled unless SMTP is configured and an employee enables email notifications. Task reminders and announcement notifications can be managed separately under **Profile & settings**.
- Google Calendar sync requires OAuth credentials and user authorization; only onboarding and assigned-task events belonging to the connected employee are synchronized.
- PDF reports use the browser print dialog; select *Save as PDF* in the print destination.

## Before production

Use HTTPS, explicit CORS origins, Alembic migrations, secure secret management, rate limiting, audit logs, backups, antivirus/content scanning for uploads, stronger document-retention policies, proper organization time zones and holidays, formal payroll/tax rules, and a full security and access-control review. Do not use demo payroll defaults for actual payments.
#   W o r k s p h e r e - H R M S 
 
 