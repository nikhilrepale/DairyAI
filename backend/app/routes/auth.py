from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Dairy, DairyMembership, User
from ..schemas import LoginIn, MembershipIn, RegisterDairyIn, SelectDairyIn
from ..security import (
    OwnerManagerDep,
    TenantDep,
    create_access_token,
    ensure_auth_configured,
    get_tenant_context,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["authentication"])


def _session_response(user: User, dairy: Dairy, membership: DairyMembership) -> dict:
    return {
        "access_token": create_access_token(user.id, dairy.id),
        "token_type": "bearer",
        "user": {"id": user.id, "name": user.name},
        "dairy": {"id": dairy.id, "name": dairy.name, "dairy_code": dairy.dairy_code},
        "role": membership.role,
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_dairy(body: RegisterDairyIn, db: Session = Depends(get_db)):
    ensure_auth_configured()
    dairy = Dairy(
        name=body.dairy_name.strip(),
        dairy_code=body.dairy_code.strip().upper(),
        address=body.address,
        village=body.village,
        phone=body.phone or "",
        preferred_language=body.preferred_language,
    )
    user = User(
        name=body.name.strip(),
        email=body.email,
        phone=body.phone,
        password_hash=hash_password(body.password),
    )
    try:
        db.add_all([dairy, user])
        db.flush()
        membership = DairyMembership(dairy_id=dairy.id, user_id=user.id, role="OWNER")
        db.add(membership)
        db.commit()
        db.refresh(user)
        db.refresh(dairy)
        db.refresh(membership)
        return _session_response(user, dairy, membership)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Dairy code, email, or phone is already registered",
        ) from exc


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    ensure_auth_configured()
    identifier = body.identifier.strip()
    user = (
        db.query(User)
        .filter(
            or_(
                func.lower(User.email) == identifier.lower(),
                User.phone == identifier,
            )
        )
        .first()
    )
    if user is None or not user.active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    memberships = (
        db.query(DairyMembership, Dairy)
        .join(Dairy, Dairy.id == DairyMembership.dairy_id)
        .filter(
            DairyMembership.user_id == user.id,
            DairyMembership.active.is_(True),
            Dairy.active.is_(True),
        )
        .all()
    )
    if body.dairy_code:
        selected = next(
            (pair for pair in memberships
             if pair[1].dairy_code.casefold() == body.dairy_code.strip().casefold()),
            None,
        )
    elif len(memberships) == 1:
        selected = memberships[0]
    else:
        selected = None
    if selected is None:
        if memberships:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Select a dairy membership using dairy_code",
                    "dairies": [
                        {"name": dairy.name, "dairy_code": dairy.dairy_code}
                        for _, dairy in memberships
                    ],
                },
            )
        raise HTTPException(status_code=403, detail="No active dairy membership")
    membership, dairy = selected
    return _session_response(user, dairy, membership)


@router.get("/me")
def current_session(context: TenantDep):
    memberships = [
        membership
        for membership in context.user.memberships
        if membership.active and membership.dairy and membership.dairy.active
    ]
    return {
        "user": {"id": context.user.id, "name": context.user.name},
        "dairy": {
            "id": context.dairy.id,
            "name": context.dairy.name,
            "dairy_code": context.dairy.dairy_code,
        },
        "role": context.membership.role,
        "memberships": [
            {
                "dairy_code": membership.dairy.dairy_code,
                "name": membership.dairy.name,
                "role": membership.role,
            }
            for membership in memberships
        ],
    }


@router.post("/select-dairy")
def select_dairy(
    body: SelectDairyIn,
    context: TenantDep,
    db: Session = Depends(get_db),
):
    result = (
        db.query(DairyMembership, Dairy)
        .join(Dairy, Dairy.id == DairyMembership.dairy_id)
        .filter(
            DairyMembership.user_id == context.user.id,
            DairyMembership.active.is_(True),
            Dairy.active.is_(True),
            func.lower(Dairy.dairy_code) == body.dairy_code.strip().lower(),
        )
        .first()
    )
    if result is None:
        raise HTTPException(status_code=403, detail="No active membership for this dairy")
    membership, dairy = result
    return _session_response(context.user, dairy, membership)


@router.get("/memberships")
def list_memberships(context: OwnerManagerDep, db: Session = Depends(get_db)):
    memberships = (
        db.query(DairyMembership, User)
        .join(User, User.id == DairyMembership.user_id)
        .filter(DairyMembership.dairy_id == context.dairy.id)
        .order_by(User.name)
        .all()
    )
    return [
        {
            "id": membership.id, "user_id": user.id, "name": user.name,
            "email": user.email, "phone": user.phone,
            "role": membership.role, "active": membership.active,
        }
        for membership, user in memberships
    ]


@router.post("/memberships", status_code=status.HTTP_201_CREATED)
def add_membership(
    body: MembershipIn,
    context: OwnerManagerDep,
    db: Session = Depends(get_db),
):
    if context.membership.role != "OWNER" and body.role != "OPERATOR":
        raise HTTPException(403, "Managers can only add operators")
    identifier = body.identifier.strip()
    user = db.query(User).filter(
        or_(func.lower(User.email) == identifier.lower(), User.phone == identifier)
    ).first()
    if user is None or not user.active:
        raise HTTPException(404, "Register the user's account before adding a membership")
    existing = db.query(DairyMembership).filter_by(
        dairy_id=context.dairy.id, user_id=user.id
    ).first()
    if existing:
        if existing.active:
            raise HTTPException(409, "User is already a member of this dairy")
        existing.active = True
        existing.role = body.role
        membership = existing
    else:
        membership = DairyMembership(
            dairy_id=context.dairy.id, user_id=user.id, role=body.role
        )
        db.add(membership)
    db.commit()
    return {"id": membership.id, "user_id": user.id, "role": membership.role}
