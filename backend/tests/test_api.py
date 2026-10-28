def register(client, username="employee1", email="employee1@example.com"):
    return client.post("/api/auth/register", json={
        "username": username, "email": email, "password": "StrongPass123!", "full_name": "Test Employee"
    })


def login(client, username="employee1", password="StrongPass123!"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_register_login_and_own_profile(client):
    created = register(client)
    assert created.status_code == 201
    assert created.json()["role"] == "EMPLOYEE"
    signed_in = login(client)
    assert signed_in.status_code == 200
    token = signed_in.json()["access_token"]
    response = client.get("/api/employees/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["full_name"] == "Test Employee"


def test_public_registration_cannot_choose_admin_role(client):
    response = client.post("/api/auth/register", json={
        "username": "badadmin", "email": "badadmin@example.com", "password": "StrongPass123!",
        "full_name": "Bad Admin", "role": "ADMIN"
    })
    assert response.status_code == 201
    assert response.json()["role"] == "EMPLOYEE"


def test_employee_cannot_view_all_employees(client):
    register(client)
    token = login(client).json()["access_token"]
    response = client.get("/api/employees", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_duplicate_checkin_is_rejected(client):
    register(client)
    token = login(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    first = client.post("/api/attendance/check-in", headers=headers)
    second = client.post("/api/attendance/check-in", headers=headers)
    assert first.status_code == 201
    assert second.status_code == 409


def test_unauthenticated_request_is_rejected(client):
    response = client.get("/api/employees/me")
    assert response.status_code == 401


def create_admin_account(username="admin1"):
    from app.database import SessionLocal
    from app.models import Employee, User
    from app.security import hash_password
    db = SessionLocal()
    try:
        user = User(username=username, email=f"{username}@example.com", hashed_password=hash_password("AdminPass123!"), role="ADMIN", is_active=True)
        db.add(user)
        db.flush()
        db.add(Employee(user_id=user.id, full_name="Test Administrator"))
        db.commit()
    finally:
        db.close()


def next_weekdays(count=2):
    from datetime import date, timedelta
    result = []
    current = date.today() + timedelta(days=1)
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def test_leave_approval_deducts_weekday_balance(client):
    register(client)
    create_admin_account()
    employee_token = login(client).json()["access_token"]
    admin_token = login(client, "admin1", "AdminPass123!").json()["access_token"]
    start, end = next_weekdays(2)
    leave = client.post("/api/leaves", headers={"Authorization": f"Bearer {employee_token}"}, json={
        "leave_type": "CASUAL", "start_date": start.isoformat(), "end_date": end.isoformat(), "reason": "Personal appointment"
    })
    assert leave.status_code == 201
    approved = client.patch(f"/api/leaves/{leave.json()['id']}/decision", headers={"Authorization": f"Bearer {admin_token}"}, json={"status": "APPROVED"})
    assert approved.status_code == 200
    balances = client.get("/api/leave-balances", headers={"Authorization": f"Bearer {employee_token}"})
    casual = next(row for row in balances.json() if row["leave_type"] == "CASUAL")
    assert casual["remaining_days"] == 10


def test_admin_can_manage_users_and_roles(client):
    create_admin_account()
    admin_token = login(client, "admin1", "AdminPass123!").json()["access_token"]
    response = client.post("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"}, json={
        "username": "newhr", "email": "newhr@example.com", "password": "AnotherPass123!", "full_name": "New HR",
        "role": "HR", "designation": "HR Manager", "department": "People", "salary": 600000
    })
    assert response.status_code == 201
    assert response.json()["role"] == "HR"
    updated = client.patch(f"/api/admin/users/{response.json()['id']}", headers={"Authorization": f"Bearer {admin_token}"}, json={"is_active": False})
    assert updated.status_code == 200
    assert updated.json()["is_active"] is False


def test_onboarding_resignation_announcements_and_tasks_permissions(client):
    register(client, "module_employee", "module_employee@example.com")
    register(client, "other_employee", "other_employee@example.com")
    create_admin_account("module_admin")
    employee_token = login(client, "module_employee").json()["access_token"]
    other_token = login(client, "other_employee").json()["access_token"]
    admin_token = login(client, "module_admin", "AdminPass123!").json()["access_token"]
    employee_headers = {"Authorization": f"Bearer {employee_token}"}
    other_headers = {"Authorization": f"Bearer {other_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    employee_id = client.get("/api/employees/me", headers=employee_headers).json()["id"]
    other_id = client.get("/api/employees/me", headers=other_headers).json()["id"]

    onboarding = client.post("/api/onboarding", headers=admin_headers, json={
        "employee_id": employee_id, "task_name": "Complete orientation", "due_date": "2026-11-05"
    })
    assert onboarding.status_code == 201
    task_id = onboarding.json()["id"]
    assert [row["id"] for row in client.get("/api/onboarding", headers=employee_headers).json()] == [task_id]
    assert client.get("/api/onboarding", headers=other_headers).json() == []
    assert client.patch(f"/api/onboarding/{task_id}", headers=other_headers, json={"status": "COMPLETED"}).status_code == 403
    assert client.patch(f"/api/onboarding/{task_id}", headers=employee_headers, json={"status": "COMPLETED"}).json()["status"] == "COMPLETED"
    assert client.post("/api/onboarding", headers=employee_headers, json={
        "employee_id": employee_id, "task_name": "Unauthorized", "due_date": "2026-11-05"
    }).status_code == 403

    resignation = client.post("/api/resignations", headers=employee_headers, json={
        "reason": "Personal reasons", "notice_period": 30
    })
    assert resignation.status_code == 201
    assert client.post("/api/resignations", headers=employee_headers, json={
        "reason": "Duplicate", "notice_period": 30
    }).status_code == 409
    decision = client.patch(f"/api/resignations/{resignation.json()['id']}/decision", headers=admin_headers, json={"status": "APPROVED"})
    assert decision.status_code == 200
    assert decision.json()["status"] == "APPROVED"
    assert client.patch(f"/api/resignations/{resignation.json()['id']}/decision", headers=admin_headers, json={"status": "REJECTED"}).status_code == 409

    announcement = client.post("/api/announcements", headers=admin_headers, json={
        "title": "Office update", "message": "The office is closed Friday."
    })
    assert announcement.status_code == 201
    assert client.get(f"/api/announcements/{announcement.json()['id']}", headers=employee_headers).json()["title"] == "Office update"
    assert client.post("/api/announcements", headers=employee_headers, json={
        "title": "Unauthorized", "message": "Employee announcement"
    }).status_code == 403

    assigned = client.post("/api/tasks", headers=admin_headers, json={
        "assigned_to": employee_id, "title": "Review handbook", "description": "Read the handbook",
        "due_date": "2026-11-10"
    })
    assert assigned.status_code == 201
    assert assigned.json()["assigned_to"] == employee_id
    assert client.patch(f"/api/tasks/{assigned.json()['id']}", headers=other_headers, json={"status": "COMPLETED"}).status_code == 403
    assert client.patch(f"/api/tasks/{assigned.json()['id']}", headers=employee_headers, json={"status": "COMPLETED"}).json()["status"] == "COMPLETED"


def test_feedback_profile_settings_password_and_csv_exports(client):
    register(client, "settings_employee", "settings_employee@example.com")
    create_admin_account("settings_admin")
    employee_token = login(client, "settings_employee").json()["access_token"]
    admin_token = login(client, "settings_admin", "AdminPass123!").json()["access_token"]
    employee_headers = {"Authorization": f"Bearer {employee_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    employee_id = client.get("/api/employees/me", headers=employee_headers).json()["id"]

    feedback = client.post("/api/feedback", headers=employee_headers, json={
        "message": "The onboarding was helpful.", "rating": 5
    })
    assert feedback.status_code == 201
    assert client.get("/api/feedback", headers=employee_headers).status_code == 403
    listed_feedback = client.get("/api/feedback", headers=admin_headers)
    assert listed_feedback.status_code == 200
    assert listed_feedback.json()[0]["employee_id"] == employee_id
    user_id = client.get("/api/employees/me", headers=employee_headers).json()["user_id"]
    assert client.delete(f"/api/admin/users/{user_id}", headers=admin_headers).status_code == 409
    invalid_feedback = client.post("/api/feedback", headers=employee_headers, json={
        "message": "Invalid rating", "rating": 8
    })
    assert invalid_feedback.status_code == 422

    profile = client.patch("/api/employees/me", headers=employee_headers, json={
        "full_name": "Updated Employee", "email": "updated_employee@example.com"
    })
    assert profile.status_code == 200
    assert profile.json()["full_name"] == "Updated Employee"
    assert profile.json()["email"] == "updated_employee@example.com"
    assert client.get("/api/settings/me", headers=employee_headers).json()["task_reminders"] is True
    preferences = client.put("/api/settings/me", headers=employee_headers, json={
        "email_notifications": True, "task_reminders": False, "announcement_notifications": True
    })
    assert preferences.status_code == 200
    assert preferences.json()["task_reminders"] is False

    assert client.post("/api/auth/change-password", headers=employee_headers, json={
        "current_password": "wrong-password", "new_password": "NewStrongPass123!"
    }).status_code == 400
    changed = client.post("/api/auth/change-password", headers=employee_headers, json={
        "current_password": "StrongPass123!", "new_password": "NewStrongPass123!"
    })
    assert changed.status_code == 200
    assert login(client, "settings_employee", "NewStrongPass123!").status_code == 200
    assert login(client, "settings_employee").status_code == 401

    client.post("/api/feedback", headers=employee_headers, json={"message": "=1+1", "rating": 3})
    exported = client.get("/api/exports/reports.csv?report_type=feedback", headers=admin_headers)
    assert exported.status_code == 200
    assert "text/csv" in exported.headers["content-type"]
    assert "The onboarding was helpful." in exported.text
    assert "'=1+1" in exported.text
    assert client.get("/api/exports/reports.csv?report_type=payroll", headers=employee_headers).status_code == 403


def test_google_calendar_oauth_sync_and_disconnect(client, monkeypatch):
    import json
    from urllib.parse import parse_qs, urlparse
    from app import routers
    from app.config import settings
    from app.models import GoogleCalendarConnection
    from app.database import SessionLocal

    monkeypatch.setattr(settings, "google_oauth_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "test-client-secret")
    register(client, "calendar_employee", "calendar_employee@example.com")
    create_admin_account("calendar_admin")
    employee_token = login(client, "calendar_employee").json()["access_token"]
    admin_token = login(client, "calendar_admin", "AdminPass123!").json()["access_token"]
    employee_headers = {"Authorization": f"Bearer {employee_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    employee_id = client.get("/api/employees/me", headers=employee_headers).json()["id"]

    integration_status = client.get("/api/integrations/status", headers=employee_headers).json()
    assert integration_status["google_calendar_configured"] is True
    assert integration_status["google_calendar_callback_origin"] == "http://127.0.0.1:8000"
    authorize = client.get("/api/integrations/google/authorize", headers=employee_headers)
    assert authorize.status_code == 200
    authorization_url = authorize.json()["authorization_url"]
    query = parse_qs(urlparse(authorization_url).query)
    assert query["scope"] == [routers.GOOGLE_CALENDAR_SCOPE]
    assert query["access_type"] == ["offline"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8000/api/integrations/google/callback"]
    state = query["state"][0]

    class FakeResponse:
        def __init__(self, value):
            self.value = json.dumps(value).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return self.value

    def exchange_code(_request, timeout):
        assert timeout == 20
        return FakeResponse({"access_token": "temporary-access-token", "refresh_token": "secret-refresh-token"})

    monkeypatch.setattr(routers, "urlopen", exchange_code)
    callback = client.get("/api/integrations/google/callback", params={"state": state, "code": "authorization-code"})
    assert callback.status_code == 200
    assert "worksphere-google-calendar" in callback.text
    with SessionLocal() as db:
        connection = db.query(GoogleCalendarConnection).filter_by(user_id=client.get(
            "/api/employees/me", headers=employee_headers
        ).json()["user_id"]).one()
        assert "secret-refresh-token" not in connection.encrypted_refresh_token
        assert routers._google_token_cipher().decrypt(connection.encrypted_refresh_token.encode()).decode() == "secret-refresh-token"
    assert client.get("/api/integrations/google/callback", params={"state": state, "code": "again"}).status_code == 400

    onboarding = client.post("/api/onboarding", headers=admin_headers, json={
        "employee_id": employee_id, "task_name": "Google calendar event", "due_date": "2026-11-15"
    })
    assert onboarding.status_code == 201
    calendar_calls = []

    def calendar_request(request, timeout):
        assert timeout == 20
        calendar_calls.append((request.get_method(), request.full_url))
        if request.full_url == routers.GOOGLE_TOKEN_URL:
            return FakeResponse({"access_token": "temporary-access-token"})
        if request.get_method() == "POST":
            return FakeResponse({"id": "google-event-1"})
        return FakeResponse({"id": "google-event-1"})

    monkeypatch.setattr(routers, "urlopen", calendar_request)
    first_sync = client.post("/api/integrations/google/sync", headers=employee_headers)
    assert first_sync.status_code == 200
    assert first_sync.json()["created"] == 1
    second_sync = client.post("/api/integrations/google/sync", headers=employee_headers)
    assert second_sync.status_code == 200
    assert second_sync.json()["updated"] == 1
    assert any(method == "PATCH" for method, _url in calendar_calls)

    assert client.patch(f"/api/onboarding/{onboarding.json()['id']}", headers=employee_headers, json={
        "status": "COMPLETED"
    }).status_code == 200
    removed = client.post("/api/integrations/google/sync", headers=employee_headers)
    assert removed.status_code == 200
    assert removed.json()["removed"] == 1
    assert any(method == "DELETE" for method, _url in calendar_calls)

    disconnected = client.delete("/api/integrations/google", headers=employee_headers)
    assert disconnected.status_code == 200
    assert client.get("/api/integrations/google/status", headers=employee_headers).json()["connected"] is False


def test_email_test_endpoint_uses_configured_smtp(client, monkeypatch):
    from app.config import settings
    from app import routers

    register(client, "email_employee", "email_employee@example.com")
    token = login(client, "email_employee").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_username", "smtp-user")
    monkeypatch.setattr(settings, "smtp_password", "smtp-password")
    monkeypatch.setattr(settings, "email_from", "hr@example.com")
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("smtp.example.com", 587, 10)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def starttls(self):
            return None

        def login(self, username, password):
            assert (username, password) == ("smtp-user", "smtp-password")

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(routers.smtplib, "SMTP", FakeSMTP)
    result = client.post("/api/integrations/email/test", headers=headers)
    assert result.status_code == 200
    assert sent[0]["To"] == "email_employee@example.com"
    assert sent[0]["From"] == "hr@example.com"
    assert "working" in sent[0].get_content()


def test_email_test_endpoint_reports_missing_smtp_configuration(client, monkeypatch):
    from app.config import settings

    register(client, "email_unconfigured", "email_unconfigured@example.com")
    token = login(client, "email_unconfigured").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(settings, "smtp_host", None)
    monkeypatch.setattr(settings, "email_from", None)

    status_response = client.get("/api/integrations/status", headers=headers)
    assert status_response.json()["email_configured"] is False
    response = client.post("/api/integrations/email/test", headers=headers)
    assert response.status_code == 503
    assert "SMTP_HOST" in response.json()["detail"]
