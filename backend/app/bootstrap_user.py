import argparse
from getpass import getpass

from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine
from app.models import Employee, User
from app.security import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create or promote a trusted WorkSphere HR/Admin account."
    )
    parser.add_argument("--username", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True, dest="full_name")
    parser.add_argument("--role", required=True, choices=("HR", "ADMIN"))
    args = parser.parse_args()

    password = getpass("New account password (minimum 10 characters): ")
    confirmation = getpass("Confirm password: ")
    if len(password) < 10:
        parser.error("Password must be at least 10 characters.")
    if password != confirmation:
        parser.error("Passwords do not match.")

    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()
    try:
        email = args.email.strip().lower()
        existing_email = db.query(User).filter(User.email == email).first()
        user = db.query(User).filter(User.username == args.username).first()
        if existing_email is not None and (user is None or existing_email.id != user.id):
            parser.error("That email address is already used by a different account.")

        if user is None:
            user = User(
                username=args.username,
                email=email,
                hashed_password=hash_password(password),
                role=args.role,
                is_active=True,
            )
            db.add(user)
            db.flush()
        else:
            user.email = email
            user.hashed_password = hash_password(password)
            user.role = args.role
            user.is_active = True

        employee = db.query(Employee).filter(Employee.user_id == user.id).first()
        if employee is None:
            db.add(Employee(user_id=user.id, full_name=args.full_name))
        else:
            employee.full_name = args.full_name

        db.commit()
        print(f"{args.role} account ready for frontend login: {user.username}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
