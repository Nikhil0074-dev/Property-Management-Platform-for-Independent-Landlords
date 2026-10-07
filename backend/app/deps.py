import json
from datetime import date, datetime

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import AuditLog, Notification, Property, Tenant, Unit, User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if not creds:
        raise HTTPException(401, "Not authenticated")
    uid = decode_token(creds.credentials)
    user = db.get(User, uid) if uid else None
    if not user or user.status != "active":
        raise HTTPException(401, "Invalid or expired session")
    return user


def require_roles(*roles):
    def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Forbidden for your role")
        return user
    return dep


def to_dict(o, exclude=()):
    d = {}
    for c in o.__table__.columns:
        if c.name in exclude:
            continue
        v = getattr(o, c.name)
        if isinstance(v, (datetime, date)):
            v = v.isoformat()
        d[c.name] = v
    return d


def audit(db, actor, action, rtype, rid=None, meta=None):
    db.add(AuditLog(actor_id=actor.id if actor else None, action=action, resource_type=rtype,
                    resource_id=str(rid) if rid is not None else None, meta=json.dumps(meta or {})))


def notify(db, user_id, ntype, title, message="", dedupe_key=None):
    if dedupe_key and db.query(Notification).filter_by(user_id=user_id, dedupe_key=dedupe_key).first():
        return
    db.add(Notification(user_id=user_id, type=ntype, title=title, message=message, dedupe_key=dedupe_key))


def landlord_of(user: User) -> int:
    return user.id if user.role == "landlord" else user.owner_id


def own_property(db, user, pid) -> Property:
    p = db.get(Property, pid)
    if not p or p.landlord_id != user.id:
        raise HTTPException(404, "Property not found")
    return p


def own_unit(db, user, uid) -> Unit:
    u = db.get(Unit, uid)
    if not u or u.property.landlord_id != user.id:
        raise HTTPException(404, "Unit not found")
    return u


def own_tenant(db, user, tid) -> Tenant:
    t = db.get(Tenant, tid)
    if not t or t.landlord_id != user.id:
        raise HTTPException(404, "Tenant not found")
    return t


def tenant_row(db, user) -> Tenant:
    t = db.query(Tenant).filter_by(user_id=user.id).first()
    if not t:
        raise HTTPException(403, "No tenant profile")
    return t
