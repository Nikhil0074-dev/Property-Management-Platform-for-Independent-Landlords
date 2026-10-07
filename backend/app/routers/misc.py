import os
import uuid
from datetime import date, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import (audit, get_current_user, landlord_of, notify, own_tenant,
                    require_roles, tenant_row, to_dict)
from ..models import (AuditLog, Document, Lease, Notification, Payment, Property,
                      RentInvoice, ScreeningRequest, Tenant, Ticket, TicketCost,
                      Unit, User, utcnow)
from ..services import storage
from .leases import LIVE, refresh_leases
from .maintenance import can_access as ticket_access
from .rent import refresh_invoices

router = APIRouter(prefix="/api", tags=["misc"])
Landlord = require_roles("landlord")

# ---------------- documents ----------------
ALLOWED = {".pdf": ("application/pdf", b"%PDF"), ".png": ("image/png", b"\x89PNG"), ".jpg": ("image/jpeg", b"\xff\xd8"),
           ".jpeg": ("image/jpeg", b"\xff\xd8")}


def doc_out(d: Document):
    return to_dict(d, ("storage_key",))


def doc_access(db, user: User, d: Document) -> bool:
    if d.owner_id == user.id or (user.role == "landlord" and d.landlord_id == user.id):
        return True
    if d.ticket_id:
        t = db.get(Ticket, d.ticket_id)
        if t and ticket_access(db, user, t):
            return True
    if d.lease_id and user.role == "tenant":
        l = db.get(Lease, d.lease_id)
        if l and l.tenant.user_id == user.id:
            return True
    return False


@router.post("/documents", status_code=201)
async def upload_document(file: UploadFile = File(...), document_type: str = Form("other"),
                          ticket_id: Optional[int] = Form(None), user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    if user.role == "admin":
        raise HTTPException(403, "Forbidden for your role")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED:
        raise HTTPException(415, "Allowed types: PDF, PNG, JPG")
    data = await file.read(settings.MAX_UPLOAD + 1)
    if len(data) > settings.MAX_UPLOAD:
        raise HTTPException(413, "File too large (max 5 MB)")
    mime, magic = ALLOWED[ext]
    if not data.startswith(magic):
        raise HTTPException(415, "File content does not match its extension")
    if ticket_id is not None:
        t = db.get(Ticket, ticket_id)
        if not t or not ticket_access(db, user, t):
            raise HTTPException(404, "Ticket not found")
    key = storage.save_bytes(data, ext)
    d = Document(owner_id=user.id, landlord_id=landlord_of(user), ticket_id=ticket_id, document_type=document_type[:40],
                 filename=os.path.basename(file.filename or "file")[:255], storage_key=key, mime_type=mime, size=len(data))
    db.add(d)
    db.flush()
    audit(db, user, "document.uploaded", "document", d.id)
    db.commit()
    return doc_out(d)


@router.get("/documents")
def list_documents(ticket_id: Optional[int] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "admin":
        raise HTTPException(403, "Forbidden for your role")
    q = db.query(Document)
    if ticket_id is not None:
        q = q.filter(Document.ticket_id == ticket_id)
    return [doc_out(d) for d in q.order_by(Document.id.desc()).all() if doc_access(db, user, d)]


@router.get("/documents/{did}/download")
def download_document(did: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    d = db.get(Document, did)
    if not d or not doc_access(db, user, d):
        raise HTTPException(404, "Document not found")
    audit(db, user, "document.downloaded", "document", d.id)
    db.commit()
    return Response(storage.read_bytes(d.storage_key), media_type=d.mime_type,
                    headers={"Content-Disposition": f'attachment; filename="{d.id}-{d.filename}"'.replace("\r", "").replace("\n", ""),
                             "X-Content-Type-Options": "nosniff"})


# ---------------- screening (mock provider) ----------------
class ScreeningIn(BaseModel):
    tenant_id: int


class ConsentIn(BaseModel):
    consent: bool


def screening_out(db, s: ScreeningRequest):
    d = to_dict(s)
    d["tenant_name"] = db.get(Tenant, s.tenant_id).user.name
    d["notice"] = "Mock data. Do not make rental decisions solely from automated results."
    return d


def get_screening(db, user, sid) -> ScreeningRequest:
    s = db.get(ScreeningRequest, sid)
    if s:
        if user.role == "landlord" and s.landlord_id == user.id:
            return s
        if user.role == "tenant" and db.get(Tenant, s.tenant_id).user_id == user.id:
            return s
    raise HTTPException(404, "Screening request not found")


@router.get("/screening")
def list_screening(user: User = Depends(require_roles("landlord", "tenant")), db: Session = Depends(get_db)):
    q = db.query(ScreeningRequest).order_by(ScreeningRequest.id.desc())
    if user.role == "landlord":
        q = q.filter_by(landlord_id=user.id)
    else:
        q = q.filter_by(tenant_id=tenant_row(db, user).id)
    return [screening_out(db, s) for s in q.all()]


@router.post("/screening", status_code=201)
def create_screening(body: ScreeningIn, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    t = own_tenant(db, user, body.tenant_id)
    s = ScreeningRequest(tenant_id=t.id, landlord_id=user.id)
    db.add(s)
    db.flush()
    notify(db, t.user_id, "screening", "Screening consent requested",
           "Your landlord requested a background check. Review and give or decline consent.")
    audit(db, user, "screening.requested", "screening", s.id)
    db.commit()
    return screening_out(db, s)


@router.get("/screening/{sid}")
def read_screening(sid: int, user: User = Depends(require_roles("landlord", "tenant")), db: Session = Depends(get_db)):
    s = get_screening(db, user, sid)
    audit(db, user, "screening.accessed", "screening", s.id)
    db.commit()
    return screening_out(db, s)


@router.post("/screening/{sid}/consent")
def consent_screening(sid: int, body: ConsentIn, user: User = Depends(require_roles("tenant")), db: Session = Depends(get_db)):
    s = get_screening(db, user, sid)
    if s.status != "awaiting_consent":
        raise HTTPException(409, "Request already handled")
    if body.consent:
        s.consent_at = utcnow()
        s.provider_reference = "scr_" + uuid.uuid4().hex[:12]
        s.result_reference = "MOCK-" + uuid.uuid4().hex[:10].upper()
        s.status = "completed"
        s.completed_at = utcnow()
        notify(db, s.landlord_id, "screening", "Screening completed", "A screening result reference is available.")
    else:
        s.status = "declined"
        notify(db, s.landlord_id, "screening", "Screening declined", "The tenant declined consent.")
    audit(db, user, "screening.consent", "screening", s.id, {"consent": body.consent})
    db.commit()
    return screening_out(db, s)


# ---------------- notifications ----------------
@router.get("/notifications")
def list_notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Notification).filter_by(user_id=user.id).order_by(Notification.id.desc()).limit(100).all()
    return [to_dict(n, ("dedupe_key",)) for n in rows]


@router.post("/notifications/{nid}/read")
def read_notification(nid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.get(Notification, nid)
    if not n or n.user_id != user.id:
        raise HTTPException(404, "Notification not found")
    n.read_at = n.read_at or utcnow()
    db.commit()
    return to_dict(n, ("dedupe_key",))


@router.post("/notifications/run-reminders")
def run_reminders(user: User = Depends(Landlord), db: Session = Depends(get_db)):
    """Generates (deduplicated) rent-due, overdue and lease-expiry reminders for this landlord's tenants."""
    refresh_leases(db)
    refresh_invoices(db)
    today, count = date.today(), 0
    for inv in db.query(RentInvoice).filter(RentInvoice.status.in_(("pending", "partially_paid", "overdue"))).all():
        l = inv.lease
        if l.unit.property.landlord_id != user.id:
            continue
        uid = l.tenant.user_id
        if inv.status == "overdue":
            notify(db, uid, "rent", "Rent overdue", f"Your rent for {inv.billing_period} is overdue.", f"overdue:{inv.id}:{today}")
            count += 1
        elif 0 <= (inv.due_date - today).days <= 3:
            notify(db, uid, "rent", "Rent due soon", f"Rent for {inv.billing_period} is due on {inv.due_date}.", f"due:{inv.id}")
            count += 1
    for l in db.query(Lease).filter(Lease.status == "expiring").all():
        if l.unit.property.landlord_id == user.id:
            msg = f"Lease for unit {l.unit.unit_number} ends on {l.end_date}."
            notify(db, l.tenant.user_id, "lease", "Lease expiring", msg, f"expiring:{l.id}:t")
            notify(db, user.id, "lease", "Lease expiring", msg, f"expiring:{l.id}:l")
            count += 1
    db.commit()
    return {"processed": count}


# ---------------- dashboard ----------------
@router.get("/dashboard")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    refresh_leases(db)
    refresh_invoices(db)
    if user.role == "landlord":
        return landlord_dashboard(db, user)
    if user.role == "tenant":
        return tenant_dashboard(db, user)
    if user.role == "maintenance":
        tix = db.query(Ticket).filter_by(assigned_to=user.id).all()
        return {"role": "maintenance", "assigned": len(tix),
                "by_status": _count(t.status for t in tix)}
    return {"role": "admin", "users": db.query(User).count(), "landlords": db.query(User).filter_by(role="landlord").count(),
            "properties": db.query(Property).count(), "audit_events": db.query(AuditLog).count()}


def _count(it):
    out = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return out


def landlord_dashboard(db, user):
    props = db.query(Property).filter_by(landlord_id=user.id).all()
    units = [u for p in props for u in p.units]
    leases = [l for l in db.query(Lease).all() if l.unit.property.landlord_id == user.id]
    live = [l for l in leases if l.status in LIVE]
    invs = [i for i in db.query(RentInvoice).all() if i.lease.unit.property.landlord_id == user.id and i.status != "cancelled"]
    total = round(sum(i.amount for i in invs), 2)
    collected = round(sum(i.amount_paid or 0 for i in invs), 2)
    overdue = [i for i in invs if i.status == "overdue"]
    tix = [t for t in db.query(Ticket).all() if t.unit.property.landlord_id == user.id]
    costs = {}
    for c in db.query(TicketCost).all():
        t = db.get(Ticket, c.ticket_id)
        if t.unit.property.landlord_id == user.id:
            costs[t.category] = round(costs.get(t.category, 0) + c.amount, 2)
    by_month = {}
    for i in invs:
        m = by_month.setdefault(i.billing_period, {"due": 0, "collected": 0})
        m["due"] += i.amount
        m["collected"] += i.amount_paid or 0
    soon = sorted(live, key=lambda l: l.end_date)[:5]
    occupied = sum(1 for u in units if u.status == "occupied")
    return {
        "role": "landlord", "properties": len(props), "units": len(units),
        "tenants": db.query(Tenant).filter_by(landlord_id=user.id).count(),
        "rent_due": total, "collected": collected, "outstanding": round(total - collected, 2),
        "overdue_tenants": len({i.lease.tenant_id for i in overdue}), "overdue_amount": round(sum(i.amount - (i.amount_paid or 0) for i in overdue), 2),
        "collection_rate": round(collected / total * 100, 1) if total else 0,
        "occupancy_rate": round(occupied / len(units) * 100, 1) if units else 0,
        "maintenance": _count(t.status for t in tix),
        "maintenance_cost_by_category": costs,
        "monthly_collection": dict(sorted(by_month.items())),
        "upcoming_expirations": [{"lease_id": l.id, "unit": l.unit.unit_number, "end_date": l.end_date.isoformat()} for l in soon],
    }


def tenant_dashboard(db, user):
    t = tenant_row(db, user)
    lease = db.query(Lease).filter(Lease.tenant_id == t.id, Lease.status.in_(LIVE)).first()
    invs = [i for i in db.query(RentInvoice).all() if i.lease.tenant_id == t.id and i.status not in ("paid", "cancelled")]
    nxt = min(invs, key=lambda i: i.due_date) if invs else None
    open_t = db.query(Ticket).filter(Ticket.tenant_id == t.id, Ticket.status != "closed").count()
    return {"role": "tenant",
            "lease": {"id": lease.id, "unit": lease.unit.unit_number, "property": lease.unit.property.name,
                      "monthly_rent": lease.monthly_rent, "end_date": lease.end_date.isoformat(), "status": lease.status} if lease else None,
            "next_due": {"invoice_id": nxt.id, "period": nxt.billing_period, "due_date": nxt.due_date.isoformat(),
                         "outstanding": round(nxt.amount - (nxt.amount_paid or 0), 2), "status": nxt.status} if nxt else None,
            "open_tickets": open_t}


# ---------------- platform admin ----------------
Admin = require_roles("admin")


@router.get("/admin/users")
def admin_users(user: User = Depends(Admin), db: Session = Depends(get_db)):
    return [to_dict(u, ("password_hash",)) for u in db.query(User).order_by(User.id).all()]


@router.patch("/admin/users/{uid}")
def admin_patch_user(uid: int, body: dict, user: User = Depends(Admin), db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "User not found")
    if u.id == user.id:
        raise HTTPException(409, "Cannot change your own status")
    if body.get("status") not in ("active", "suspended"):
        raise HTTPException(422, "status must be active or suspended")
    u.status = body["status"]
    audit(db, user, "admin.user_status", "user", u.id, {"status": u.status})
    db.commit()
    return to_dict(u, ("password_hash",))


@router.get("/admin/audit-logs")
def admin_audit(limit: int = 100, user: User = Depends(Admin), db: Session = Depends(get_db)):
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(min(max(limit, 1), 500)).all()
    return [to_dict(r) for r in rows]
