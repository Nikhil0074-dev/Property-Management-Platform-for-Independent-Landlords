import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import audit, get_current_user, to_dict
from ..models import PasswordReset, User, utcnow
from ..security import create_token, hash_password, sha256, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ResetRequestIn(BaseModel):
    email: EmailStr


class ResetConfirmIn(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/register", status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    """Self-registration creates landlord accounts. Tenants/staff are created by invitation."""
    email = body.email.lower()
    if db.query(User).filter_by(email=email).first():
        raise HTTPException(409, "Email already registered")
    u = User(name=body.name.strip(), email=email, password_hash=hash_password(body.password), role="landlord")
    db.add(u)
    db.flush()
    audit(db, u, "user.registered", "user", u.id)
    db.commit()
    return {"user": to_dict(u, ("password_hash",)), "token": create_token(u.id)}


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    u = db.query(User).filter_by(email=body.email.lower()).first()
    if not u or not verify_password(body.password, u.password_hash) or u.status != "active":
        raise HTTPException(401, "Invalid credentials")
    audit(db, u, "user.login", "user", u.id)
    db.commit()
    return {"user": to_dict(u, ("password_hash",)), "token": create_token(u.id)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return to_dict(user, ("password_hash",))


@router.post("/password-reset/request")
def reset_request(body: ResetRequestIn, db: Session = Depends(get_db)):
    u = db.query(User).filter_by(email=body.email.lower()).first()
    out = {"message": "If the account exists, a reset link has been sent."}
    if u and u.status == "active":
        token = secrets.token_urlsafe(32)
        db.add(PasswordReset(user_id=u.id, token_hash=sha256(token), expires_at=utcnow() + timedelta(hours=1)))
        audit(db, u, "password.reset_requested", "user", u.id)
        db.commit()
        # Production: email the token. In DEBUG it is returned for local testing.
        if settings.DEBUG:
            out["dev_token"] = token
    return out


@router.post("/password-reset/confirm")
def reset_confirm(body: ResetConfirmIn, db: Session = Depends(get_db)):
    r = db.query(PasswordReset).filter_by(token_hash=sha256(body.token)).first()
    if not r or r.used or r.expires_at < utcnow():
        raise HTTPException(400, "Invalid or expired token")
    u = db.get(User, r.user_id)
    u.password_hash = hash_password(body.new_password)
    r.used = 1
    audit(db, u, "password.reset_completed", "user", u.id)
    db.commit()
    return {"message": "Password updated"}


@router.post("/change-password")
def change_password(body: ChangePasswordIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    audit(db, user, "password.changed", "user", user.id)
    db.commit()
    return {"message": "Password updated"}
