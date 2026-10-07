from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import (audit, get_current_user, notify, own_unit, require_roles,
                    tenant_row, to_dict)
from ..models import Lease, Tenant, Ticket, TicketComment, TicketCost, User
from .leases import LIVE

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])

Priority = Literal["low", "medium", "high", "critical"]
Category = Literal["plumbing", "electrical", "cleaning", "appliance", "structural", "other"]
STATUSES = ("open", "assigned", "in_progress", "waiting_for_tenant", "resolved", "closed")
TRANSITIONS = {
    "open": {"assigned", "closed"},
    "assigned": {"in_progress", "open", "closed"},
    "in_progress": {"waiting_for_tenant", "resolved"},
    "waiting_for_tenant": {"in_progress", "resolved"},
    "resolved": {"closed", "in_progress"},
    "closed": set(),
}
STAFF_ALLOWED = {"in_progress", "waiting_for_tenant", "resolved"}
SAFETY = "If there is an immediate danger to life or safety (fire, gas leak, electrical shock), contact emergency services first."


class TicketIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    category: Category = "other"
    priority: Priority = "medium"
    unit_id: Optional[int] = None  # landlords only; tenants use their active lease unit


class TicketPatch(BaseModel):
    status: Optional[Literal[STATUSES]] = None
    priority: Optional[Priority] = None
    category: Optional[Category] = None
    assigned_to: Optional[int] = None


class CostIn(BaseModel):
    description: str = Field(default="", max_length=200)
    amount: float = Field(gt=0)
    cost_type: Literal["labor", "parts", "other"] = "other"


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


def can_access(db, user: User, t: Ticket) -> bool:
    if user.role == "landlord":
        return t.unit.property.landlord_id == user.id
    if user.role == "tenant":
        ten = db.get(Tenant, t.tenant_id) if t.tenant_id else None
        return ten is not None and ten.user_id == user.id
    if user.role == "maintenance":
        return t.assigned_to == user.id
    return False


def get_ticket(db, user, tid) -> Ticket:
    t = db.get(Ticket, tid)
    if not t or not can_access(db, user, t):
        raise HTTPException(404, "Ticket not found")
    return t


def ticket_out(db, t: Ticket):
    d = to_dict(t)
    d["number"] = f"MNT-{1000 + t.id}"
    d["unit_number"] = t.unit.unit_number
    d["property_name"] = t.unit.property.name
    d["assigned_name"] = db.get(User, t.assigned_to).name if t.assigned_to else None
    if t.priority in ("high", "critical"):
        d["safety_notice"] = SAFETY
    return d


@router.get("")
def list_tickets(status: Optional[str] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "admin":
        raise HTTPException(403, "Forbidden for your role")
    rows = [t for t in db.query(Ticket).order_by(Ticket.id.desc()).all() if can_access(db, user, t)]
    if status:
        rows = [t for t in rows if t.status == status]
    return [ticket_out(db, t) for t in rows]


@router.post("", status_code=201)
def create_ticket(body: TicketIn, user: User = Depends(require_roles("tenant", "landlord")), db: Session = Depends(get_db)):
    if user.role == "tenant":
        ten = tenant_row(db, user)
        lease = (db.query(Lease).filter(Lease.tenant_id == ten.id, Lease.status.in_(LIVE)).first())
        if not lease:
            raise HTTPException(409, "No active lease; cannot submit a request")
        unit_id, tenant_id = lease.unit_id, ten.id
    else:
        if not body.unit_id:
            raise HTTPException(422, "unit_id is required")
        own_unit(db, user, body.unit_id)
        unit_id, tenant_id = body.unit_id, None
    t = Ticket(unit_id=unit_id, tenant_id=tenant_id, title=body.title, description=body.description,
               category=body.category, priority=body.priority)
    db.add(t)
    db.flush()
    landlord_id = t.unit.property.landlord_id
    if user.role == "tenant":
        notify(db, landlord_id, "maintenance", "New maintenance request", f"{t.title} (unit {t.unit.unit_number})")
    audit(db, user, "ticket.created", "ticket", t.id)
    db.commit()
    return ticket_out(db, t)


@router.get("/{tid}")
def read_ticket(tid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = get_ticket(db, user, tid)
    d = ticket_out(db, t)
    d["comments"] = comments_out(db, t)
    if user.role == "landlord":
        d["costs"] = [to_dict(c) for c in db.query(TicketCost).filter_by(ticket_id=t.id).all()]
        d["total_cost"] = round(sum(c["amount"] for c in d["costs"]), 2)
    return d


@router.patch("/{tid}")
def update_ticket(tid: int, body: TicketPatch, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = get_ticket(db, user, tid)
    data = body.model_dump(exclude_unset=True)
    new_status = data.get("status")
    if user.role == "tenant":
        if set(data) - {"status"} or (new_status and not (t.status == "resolved" and new_status == "closed")):
            raise HTTPException(403, "Tenants may only close resolved tickets")
    elif user.role == "maintenance":
        if set(data) - {"status"} or (new_status and new_status not in STAFF_ALLOWED):
            raise HTTPException(403, "Staff may only update progress status")
    elif user.role != "landlord":
        raise HTTPException(403, "Forbidden for your role")
    if "assigned_to" in data and data["assigned_to"] is not None:
        staff = db.get(User, data["assigned_to"])
        if user.role != "landlord" or not staff or staff.role != "maintenance" or staff.owner_id != user.id:
            raise HTTPException(422, "Invalid maintenance staff")
        t.assigned_to = staff.id
        if t.status == "open" and not new_status:
            new_status = "assigned"
        notify(db, staff.id, "maintenance", "Ticket assigned", f"{ticket_label(t)} was assigned to you.")
        if t.tenant_id:
            notify(db, db.get(Tenant, t.tenant_id).user_id, "maintenance", "Request assigned",
                   f"Your maintenance request {ticket_label(t)} has been assigned.")
        audit(db, user, "ticket.assigned", "ticket", t.id, {"to": staff.id})
    if new_status and new_status != t.status:
        if new_status not in TRANSITIONS[t.status]:
            raise HTTPException(409, f"Cannot change status from {t.status} to {new_status}")
        if new_status == "assigned" and not t.assigned_to:
            raise HTTPException(409, "Assign a staff member first")
        t.status = new_status
        audit(db, user, "ticket.status_changed", "ticket", t.id, {"status": new_status})
        landlord_id = t.unit.property.landlord_id
        if user.id != landlord_id:
            notify(db, landlord_id, "maintenance", "Ticket updated", f"{ticket_label(t)} is now {new_status}.")
        if t.tenant_id:
            notify(db, db.get(Tenant, t.tenant_id).user_id, "maintenance", "Request update",
                   f"Your request {ticket_label(t)} is now {new_status}.")
    for k in ("priority", "category"):
        if k in data and data[k] and user.role == "landlord":
            setattr(t, k, data[k])
    db.commit()
    return ticket_out(db, t)


def ticket_label(t: Ticket) -> str:
    return f"MNT-{1000 + t.id}"


def comments_out(db, t):
    out = []
    for c in db.query(TicketComment).filter_by(ticket_id=t.id).order_by(TicketComment.id).all():
        d = to_dict(c)
        d["author"] = db.get(User, c.user_id).name
        out.append(d)
    return out


@router.post("/{tid}/comments", status_code=201)
def add_comment(tid: int, body: CommentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = get_ticket(db, user, tid)
    db.add(TicketComment(ticket_id=t.id, user_id=user.id, body=body.body))
    audit(db, user, "ticket.commented", "ticket", t.id)
    db.commit()
    return comments_out(db, t)


@router.get("/{tid}/costs")
def list_costs(tid: int, user: User = Depends(require_roles("landlord")), db: Session = Depends(get_db)):
    t = get_ticket(db, user, tid)
    rows = db.query(TicketCost).filter_by(ticket_id=t.id).all()
    return {"costs": [to_dict(c) for c in rows], "total": round(sum(c.amount for c in rows), 2)}


@router.post("/{tid}/costs", status_code=201)
def add_cost(tid: int, body: CostIn, user: User = Depends(require_roles("landlord")), db: Session = Depends(get_db)):
    t = get_ticket(db, user, tid)
    c = TicketCost(ticket_id=t.id, **body.model_dump())
    db.add(c)
    db.flush()
    audit(db, user, "ticket.cost_added", "ticket", t.id, {"amount": body.amount})
    db.commit()
    return to_dict(c)
