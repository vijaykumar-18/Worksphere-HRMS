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
