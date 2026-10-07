import secrets
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import (audit, notify, own_property, own_tenant, own_unit,
                    require_roles, to_dict)
from ..models import Lease, Property, Tenant, Unit, User
from ..security import hash_password

router = APIRouter(prefix="/api", tags=["properties"])
Landlord = require_roles("landlord")

PropType = Literal["apartment", "house", "commercial", "room"]


class PropertyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    address: str = Field(min_length=1)
    property_type: PropType = "apartment"
    status: Literal["active", "inactive"] = "active"


class PropertyPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    address: Optional[str] = Field(default=None, min_length=1)
    property_type: Optional[PropType] = None
    status: Optional[Literal["active", "inactive"]] = None


class UnitIn(BaseModel):
    property_id: int
    unit_number: str = Field(min_length=1, max_length=40)
    monthly_rent: float = Field(gt=0)
    security_deposit: float = Field(default=0, ge=0)
    amenities: str = ""


class UnitPatch(BaseModel):
    unit_number: Optional[str] = Field(default=None, min_length=1, max_length=40)
    monthly_rent: Optional[float] = Field(default=None, gt=0)
    security_deposit: Optional[float] = Field(default=None, ge=0)
    amenities: Optional[str] = None
    status: Optional[Literal["vacant", "occupied", "maintenance"]] = None


class TenantIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    phone: str = ""


class StaffIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr


def property_out(p: Property):
    d = to_dict(p)
    d["units_count"] = len(p.units)
    return d


# ---------- properties ----------
@router.get("/properties")
def list_properties(user: User = Depends(Landlord), db: Session = Depends(get_db)):
    rows = db.query(Property).filter_by(landlord_id=user.id).order_by(Property.id).all()
    return [property_out(p) for p in rows]


@router.post("/properties", status_code=201)
def create_property(body: PropertyIn, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    p = Property(landlord_id=user.id, **body.model_dump())
    db.add(p)
    db.flush()
    audit(db, user, "property.created", "property", p.id)
    db.commit()
    return property_out(p)


@router.get("/properties/{pid}")
def get_property(pid: int, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    p = own_property(db, user, pid)
    d = property_out(p)
    d["units"] = [to_dict(u) for u in p.units]
    return d


@router.patch("/properties/{pid}")
def patch_property(pid: int, body: PropertyPatch, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    p = own_property(db, user, pid)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(p, k, v)
    audit(db, user, "property.updated", "property", p.id)
    db.commit()
    return property_out(p)


@router.delete("/properties/{pid}", status_code=204)
def delete_property(pid: int, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    p = own_property(db, user, pid)
    if p.units:
        raise HTTPException(409, "Remove or reassign units first")
    audit(db, user, "property.deleted", "property", p.id)
    db.delete(p)
    db.commit()


# ---------- units ----------
@router.get("/units")
def list_units(property_id: Optional[int] = None, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    q = db.query(Unit).join(Property).filter(Property.landlord_id == user.id)
    if property_id:
        q = q.filter(Unit.property_id == property_id)
    out = []
    for u in q.order_by(Unit.id).all():
        d = to_dict(u)
        d["property_name"] = u.property.name
        out.append(d)
    return out


@router.post("/units", status_code=201)
def create_unit(body: UnitIn, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    own_property(db, user, body.property_id)
    u = Unit(**body.model_dump())
    db.add(u)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Unit number already exists in this property")
    audit(db, user, "unit.created", "unit", u.id)
    db.commit()
    return to_dict(u)


@router.patch("/units/{uid}")
def patch_unit(uid: int, body: UnitPatch, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    u = own_unit(db, user, uid)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(u, k, v)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Unit number already exists in this property")
    audit(db, user, "unit.updated", "unit", u.id)
    db.commit()
    return to_dict(u)


# ---------- tenants ----------
def tenant_out(db, t: Tenant):
    d = to_dict(t)
    d.update(name=t.user.name, email=t.user.email)
    lease = (db.query(Lease).filter(Lease.tenant_id == t.id, Lease.status.in_(("active", "expiring")))
             .order_by(Lease.id.desc()).first())
    d["current_unit"] = lease.unit.unit_number if lease else None
    d["lease_end"] = lease.end_date.isoformat() if lease else None
    d["monthly_rent"] = lease.monthly_rent if lease else None
    return d


@router.get("/tenants")
def list_tenants(user: User = Depends(Landlord), db: Session = Depends(get_db)):
    return [tenant_out(db, t) for t in db.query(Tenant).filter_by(landlord_id=user.id).order_by(Tenant.id).all()]


@router.post("/tenants", status_code=201)
def invite_tenant(body: TenantIn, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.query(User).filter_by(email=email).first():
        raise HTTPException(409, "Email already registered")
    temp = secrets.token_urlsafe(9)
    u = User(name=body.name.strip(), email=email, password_hash=hash_password(temp), role="tenant", owner_id=user.id)
    db.add(u)
    db.flush()
    t = Tenant(user_id=u.id, landlord_id=user.id, phone=body.phone)
    db.add(t)
    db.flush()
    notify(db, u.id, "invitation", "Welcome", f"{user.name} added you as a tenant. Please change your password.")
    audit(db, user, "tenant.added", "tenant", t.id)
    db.commit()
    out = tenant_out(db, t)
    # In production the credentials are emailed via an invitation link; returned here for the demo.
    out["temp_password"] = temp
    return out


@router.get("/tenants/{tid}")
def get_tenant(tid: int, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    return tenant_out(db, own_tenant(db, user, tid))


@router.patch("/tenants/{tid}")
def patch_tenant(tid: int, body: dict, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    t = own_tenant(db, user, tid)
    if body.get("status") in ("active", "inactive"):
        t.status = body["status"]
        t.user.status = body["status"]
    if isinstance(body.get("phone"), str):
        t.phone = body["phone"][:30]
    audit(db, user, "tenant.updated", "tenant", t.id)
    db.commit()
    return tenant_out(db, t)


# ---------- maintenance staff ----------
@router.get("/staff")
def list_staff(user: User = Depends(Landlord), db: Session = Depends(get_db)):
    rows = db.query(User).filter_by(owner_id=user.id, role="maintenance").all()
    return [to_dict(u, ("password_hash",)) for u in rows]


@router.post("/staff", status_code=201)
def create_staff(body: StaffIn, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.query(User).filter_by(email=email).first():
        raise HTTPException(409, "Email already registered")
    temp = secrets.token_urlsafe(9)
    u = User(name=body.name.strip(), email=email, password_hash=hash_password(temp), role="maintenance", owner_id=user.id)
    db.add(u)
    db.flush()
    audit(db, user, "staff.added", "user", u.id)
    db.commit()
    out = to_dict(u, ("password_hash",))
    out["temp_password"] = temp
    return out
