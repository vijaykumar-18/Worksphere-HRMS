import os

# Configure an isolated test database before importing the application modules.
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-testing"
os.environ["DATABASE_URL"] = "sqlite:///./test_worksphere.db"
os.environ["ACCESS_TOKEN_MINUTES"] = "30"

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import Base, engine, SessionLocal
from app.models import LeavePolicy

@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        for leave_type, days in {"SICK": 10, "CASUAL": 12, "PAID": 15}.items():
            db.add(LeavePolicy(leave_type=leave_type, total_days_allowed=days))
        db.commit()
    finally:
        db.close()
    with TestClient(app) as test_client:
        yield test_client
    Base.metadata.drop_all(bind=engine)
