import argparse
from getpass import getpass

from sqlalchemy import func, or_

from .database import SessionLocal
from .models import Dairy, DairyMembership, User
from .security import hash_password


def main():
    parser = argparse.ArgumentParser(
        description="Create an OWNER membership for the migrated demo dairy."
    )
    parser.add_argument("--name", required=True, help="Owner's display name")
    parser.add_argument("--email", help="Owner's email address")
    parser.add_argument("--phone", help="Owner's phone number")
    parser.add_argument("--dairy-code", default="DAIRYAI-DEMO")
    args = parser.parse_args()
    email = args.email.strip().lower() if args.email else None
    phone = args.phone.strip() if args.phone else None
    if not email and not phone:
        parser.error("Provide --email or --phone")

    password = getpass("New owner password (12+ characters): ")
    if len(password) < 12:
        parser.error("Password must contain at least 12 characters")
    if password != getpass("Confirm password: "):
        parser.error("Passwords do not match")

    db = SessionLocal()
    try:
        dairy = db.query(Dairy).filter(
            func.lower(Dairy.dairy_code) == args.dairy_code.strip().lower()
        ).first()
        if dairy is None:
            parser.error(f"Dairy code {args.dairy_code} was not found; run migrations first")
        query = db.query(User)
        conditions = []
        if email:
            conditions.append(func.lower(User.email) == email)
        if phone:
            conditions.append(User.phone == phone)
        user = query.filter(or_(*conditions)).first()
        if user is None:
            user = User(
                name=args.name.strip(),
                email=email,
                phone=phone,
                password_hash=hash_password(password),
            )
            db.add(user)
            db.flush()
        elif not user.active:
            parser.error("The matching user account is inactive")
        membership = db.query(DairyMembership).filter_by(
            dairy_id=dairy.id, user_id=user.id
        ).first()
        if membership and membership.active:
            parser.error("This user already has an active membership in that dairy")
        if membership:
            membership.active = True
            membership.role = "OWNER"
        else:
            db.add(DairyMembership(dairy_id=dairy.id, user_id=user.id, role="OWNER"))
        db.commit()
        print(f"OWNER membership created for {user.email or user.phone} in {dairy.dairy_code}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
