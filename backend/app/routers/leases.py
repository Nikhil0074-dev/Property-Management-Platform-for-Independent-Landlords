import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import (audit, get_current_user, notify, own_tenant, own_unit,
                    require_roles, to_dict)
from ..models import Document, Lease, Tenant, User, utcnow
from ..services import storage
from ..services.pdf import make_pdf

router = APIRouter(prefix="/api/leases", tags=["leases"])
LIVE = ("active", "expiring")


class LeaseIn(BaseModel):
    unit_id: int
    tenant_id: int
    start_date: date
    end_date: date
    monthly_rent: float = Field(gt=0)
    deposit: float = Field(default=0, ge=0)
    due_day: int = Field(default=5, ge=1, le=28)
    late_fee_policy: str = ""
    maintenance_terms: str = ""
    additional_terms: str = ""

    @model_validator(mode="after")
    def _dates(self):
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class RenewIn(BaseModel):
    end_date: date
    monthly_rent: Optional[float] = Field(default=None, gt=0)


def refresh_leases(db: Session):
    """Move live leases between active/expiring/expired based on today's date."""
    today = date.today()
    for l in db.query(Lease).filter(Lease.status.in_(LIVE)).all():
        if l.end_date < today:
            l.status = "expired"
            l.unit.status = "vacant"
        else:
            l.status = "expiring" if (l.end_date - today).days <= 30 else "active"
    db.commit()


def lease_out(l: Lease):
    d = to_dict(l)
    d.update(unit_number=l.unit.unit_number, property_name=l.unit.property.name,
             tenant_name=l.tenant.user.name)
    return d


def get_lease_for(db, user: User, lid: int) -> Lease:
    l = db.get(Lease, lid)
    ok = False
    if l:
        if user.role == "landlord":
            ok = l.unit.property.landlord_id == user.id
        elif user.role == "tenant":
            ok = l.tenant.user_id == user.id
    if not ok:
        raise HTTPException(404, "Lease not found")
    return l


def lease_pdf_bytes(l: Lease) -> bytes:
    lines = [
        f"Lease #{l.id}   Status: {l.status}",
        f"Landlord: {l.unit.property.landlord_id}   Property: {l.unit.property.name}",
        f"Address: {l.unit.property.address}",
        f"Unit: {l.unit.unit_number}",
        f"Tenant: {l.tenant.user.name} ({l.tenant.user.email})",
        f"Term: {l.start_date} to {l.end_date}",
        f"Monthly rent: INR {l.monthly_rent:,.2f}   Due day: {l.due_day}",
        f"Security deposit: INR {l.deposit:,.2f}",
        "", "Late payment policy:", l.late_fee_policy or "-",
        "", "Maintenance responsibilities:", l.maintenance_terms or "-",
        "", "Additional terms:", l.additional_terms or "-",
        "", f"E-sign provider: {l.esign_provider}   Envelope: {l.esign_envelope_id or '-'}",
        f"Tenant signed: {l.tenant_signed_at or 'pending'}",
        f"Landlord signed: {l.landlord_signed_at or 'pending'}",
        "", "Legal sufficiency of electronic signatures varies by jurisdiction.",
    ]
    return make_pdf("Residential Lease Agreement", lines)


@router.get("")
def list_leases(user: User = Depends(require_roles("landlord", "tenant")), db: Session = Depends(get_db)):
    refresh_leases(db)
    q = db.query(Lease)
    rows = [l for l in q.order_by(Lease.id.desc()).all()
            if (user.role == "landlord" and l.unit.property.landlord_id == user.id)
            or (user.role == "tenant" and l.tenant.user_id == user.id)]
    return [lease_out(l) for l in rows]


@router.post("", status_code=201)
def create_lease(body: LeaseIn, user: User = Depends(require_roles("landlord")), db: Session = Depends(get_db)):
    unit = own_unit(db, user, body.unit_id)
    own_tenant(db, user, body.tenant_id)
    clash = db.query(Lease).filter(Lease.unit_id == unit.id, Lease.status.in_(LIVE + ("sent", "tenant_signed"))).first()
    if clash:
        raise HTTPException(409, "Unit already has a pending or active lease")
    l = Lease(**body.model_dump(), status="draft")
    db.add(l)
    db.flush()
    audit(db, user, "lease.created", "lease", l.id)
    db.commit()
    return lease_out(l)


@router.get("/{lid}")
def get_lease(lid: int, user: User = Depends(require_roles("landlord", "tenant")), db: Session = Depends(get_db)):
    refresh_leases(db)
    return lease_out(get_lease_for(db, user, lid))


@router.post("/{lid}/send")
def send_lease(lid: int, user: User = Depends(require_roles("landlord")), db: Session = Depends(get_db)):
    l = get_lease_for(db, user, lid)
    if l.status != "draft":
        raise HTTPException(409, "Only draft leases can be sent")
    l.status = "sent"
    l.esign_envelope_id = "env_" + uuid.uuid4().hex[:16]
    notify(db, l.tenant.user_id, "lease", "Lease signature request", f"A lease for unit {l.unit.unit_number} awaits your signature.")
    audit(db, user, "lease.sent", "lease", l.id, {"envelope": l.esign_envelope_id})
    db.commit()
    return lease_out(l)


@router.post("/{lid}/sign")
def sign_lease(lid: int, user: User = Depends(require_roles("landlord", "tenant")), db: Session = Depends(get_db)):
    l = get_lease_for(db, user, lid)
    now = utcnow()
    if user.role == "tenant":
        if l.status != "sent":
            raise HTTPException(409, "Lease is not awaiting tenant signature")
        l.tenant_signed_at = now
        l.status = "tenant_signed"
        notify(db, l.unit.property.landlord_id, "lease", "Tenant signed", f"{l.tenant.user.name} signed the lease for {l.unit.unit_number}.")
    else:
        if l.status != "tenant_signed":
            raise HTTPException(409, "Tenant must sign first")
        l.landlord_signed_at = now
        l.status = "active"
        l.unit.status = "occupied"
        data = lease_pdf_bytes(l)
        key = storage.save_bytes(data, ".pdf")
        doc = Document(owner_id=user.id, landlord_id=user.id, lease_id=l.id, document_type="lease_signed",
                       filename=f"lease-{l.id}-signed.pdf", storage_key=key, mime_type="application/pdf", size=len(data))
        db.add(doc)
        db.flush()
        l.document_id = doc.id
        notify(db, l.tenant.user_id, "document", "Signed lease available", "Your signed lease document is ready.")
    audit(db, user, "lease.signed", "lease", l.id, {"by": user.role})
    db.commit()
    refresh_leases(db)
    return lease_out(l)


@router.get("/{lid}/pdf")
def lease_pdf(lid: int, user: User = Depends(require_roles("landlord", "tenant")), db: Session = Depends(get_db)):
    l = get_lease_for(db, user, lid)
    return Response(lease_pdf_bytes(l), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="lease-{l.id}.pdf"'})


@router.post("/{lid}/renew", status_code=201)
def renew_lease(lid: int, body: RenewIn, user: User = Depends(require_roles("landlord")), db: Session = Depends(get_db)):
    old = get_lease_for(db, user, lid)
    if old.status not in LIVE + ("expired",):
        raise HTTPException(409, "Only active, expiring or expired leases can be renewed")
    if body.end_date <= old.end_date:
        raise HTTPException(422, "New end date must be after the current end date")
    new = Lease(unit_id=old.unit_id, tenant_id=old.tenant_id, start_date=old.end_date, end_date=body.end_date,
                monthly_rent=body.monthly_rent or old.monthly_rent, deposit=old.deposit, due_day=old.due_day,
                late_fee_policy=old.late_fee_policy, maintenance_terms=old.maintenance_terms,
                additional_terms=old.additional_terms, status="draft")
    old.status = "renewed"
    db.add(new)
    db.flush()
    audit(db, user, "lease.renewed", "lease", old.id, {"new_lease": new.id})
    db.commit()
    return lease_out(new)
