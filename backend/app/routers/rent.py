import hashlib
import hmac
import json
import re
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import audit, notify, require_roles, tenant_row, to_dict
from ..models import Lease, Payment, ProcessedEvent, Property, RentInvoice, Unit, User, utcnow
from ..services.pdf import make_pdf
from .leases import LIVE, refresh_leases

router = APIRouter(prefix="/api", tags=["rent"])
Landlord = require_roles("landlord")
Both = require_roles("landlord", "tenant")
PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class InvoiceIn(BaseModel):
    lease_id: int
    billing_period: str


class GenerateIn(BaseModel):
    billing_period: str


class PayIn(BaseModel):
    invoice_id: int
    amount: Optional[float] = Field(default=None, gt=0)


class SimulateIn(BaseModel):
    outcome: str = "success"


def settle(inv: RentInvoice):
    if inv.status == "cancelled":
        return
    paid = round(inv.amount_paid or 0, 2)
    if paid >= inv.amount:
        inv.status = "paid"
    elif inv.due_date < date.today():
        inv.status = "overdue"
    elif paid > 0:
        inv.status = "partially_paid"
    else:
        inv.status = "pending"
    # a partially paid invoice past due is reported as overdue (outstanding balance)


def refresh_invoices(db: Session):
    for inv in db.query(RentInvoice).filter(RentInvoice.status.in_(("pending", "partially_paid", "overdue"))).all():
        settle(inv)
    db.commit()


def invoice_out(inv: RentInvoice):
    d = to_dict(inv)
    l = inv.lease
    d.update(unit_number=l.unit.unit_number, property_name=l.unit.property.name,
             tenant_name=l.tenant.user.name, outstanding=round(max(inv.amount - (inv.amount_paid or 0), 0), 2)
             if inv.status != "cancelled" else 0)
    return d


def scoped_invoices(db, user):
    refresh_leases(db)
    refresh_invoices(db)
    out = []
    for inv in db.query(RentInvoice).order_by(RentInvoice.id.desc()).all():
        l = inv.lease
        if (user.role == "landlord" and l.unit.property.landlord_id == user.id) or \
           (user.role == "tenant" and l.tenant.user_id == user.id):
            out.append(inv)
    return out


def get_invoice_for(db, user, iid) -> RentInvoice:
    inv = db.get(RentInvoice, iid)
    if inv:
        l = inv.lease
        if (user.role == "landlord" and l.unit.property.landlord_id == user.id) or \
           (user.role == "tenant" and l.tenant.user_id == user.id):
            return inv
    raise HTTPException(404, "Invoice not found")


def make_invoice(db, lease: Lease, period: str):
    y, m = map(int, period.split("-"))
    inv = RentInvoice(lease_id=lease.id, billing_period=period, amount=lease.monthly_rent,
                      due_date=date(y, m, min(lease.due_day, 28)))
    settle(inv)
    return inv


# ---------- invoices ----------
@router.get("/rent/invoices")
def list_invoices(status: Optional[str] = None, lease_id: Optional[int] = None,
                  user: User = Depends(Both), db: Session = Depends(get_db)):
    rows = scoped_invoices(db, user)
    if status:
        rows = [r for r in rows if r.status == status]
    if lease_id:
        rows = [r for r in rows if r.lease_id == lease_id]
    return [invoice_out(r) for r in rows]


@router.post("/rent/invoices", status_code=201)
def create_invoice(body: InvoiceIn, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    if not PERIOD.match(body.billing_period):
        raise HTTPException(422, "billing_period must be YYYY-MM")
    lease = db.get(Lease, body.lease_id)
    if not lease or lease.unit.property.landlord_id != user.id:
        raise HTTPException(404, "Lease not found")
    if lease.status not in LIVE:
        raise HTTPException(409, "Lease is not active")
    inv = make_invoice(db, lease, body.billing_period)
    db.add(inv)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Invoice already exists for this period")
    notify(db, lease.tenant.user_id, "rent", f"Rent invoice {inv.billing_period}",
           f"Rent of INR {inv.amount:,.2f} is due on {inv.due_date}.")
    audit(db, user, "invoice.created", "invoice", inv.id)
    db.commit()
    return invoice_out(inv)


@router.post("/rent/invoices/generate")
def generate_invoices(body: GenerateIn, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    if not PERIOD.match(body.billing_period):
        raise HTTPException(422, "billing_period must be YYYY-MM")
    refresh_leases(db)
    created, skipped = [], 0
    leases = [l for l in db.query(Lease).filter(Lease.status.in_(LIVE)).all() if l.unit.property.landlord_id == user.id]
    for l in leases:
        if db.query(RentInvoice).filter_by(lease_id=l.id, billing_period=body.billing_period).first():
            skipped += 1
            continue
        inv = make_invoice(db, l, body.billing_period)
        db.add(inv)
        db.flush()
        notify(db, l.tenant.user_id, "rent", f"Rent invoice {inv.billing_period}",
               f"Rent of INR {inv.amount:,.2f} is due on {inv.due_date}.")
        created.append(inv.id)
    audit(db, user, "invoice.batch_generated", "invoice", None, {"period": body.billing_period, "count": len(created)})
    db.commit()
    return {"created": len(created), "skipped": skipped, "invoice_ids": created}


@router.get("/rent/invoices/{iid}")
def get_invoice(iid: int, user: User = Depends(Both), db: Session = Depends(get_db)):
    inv = get_invoice_for(db, user, iid)
    settle(inv)
    db.commit()
    return invoice_out(inv)


@router.post("/rent/invoices/{iid}/cancel")
def cancel_invoice(iid: int, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    inv = get_invoice_for(db, user, iid)
    if (inv.amount_paid or 0) > 0:
        raise HTTPException(409, "Invoice has payments; refund first")
    inv.status = "cancelled"
    audit(db, user, "invoice.cancelled", "invoice", inv.id)
    db.commit()
    return invoice_out(inv)


@router.post("/rent/invoices/{iid}/mark-paid")
def mark_paid(iid: int, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    """Record an offline payment (cash/bank/UPI) for the outstanding balance."""
    inv = get_invoice_for(db, user, iid)
    if inv.status in ("paid", "cancelled"):
        raise HTTPException(409, f"Invoice is {inv.status}")
    due = round(inv.amount - (inv.amount_paid or 0), 2)
    p = Payment(invoice_id=inv.id, tenant_id=inv.lease.tenant_id, amount=due, provider="manual",
                provider_reference="man_" + uuid.uuid4().hex[:16], status="succeeded", paid_at=utcnow())
    db.add(p)
    inv.amount_paid = round((inv.amount_paid or 0) + due, 2)
    settle(inv)
    db.flush()
    notify(db, inv.lease.tenant.user_id, "rent", "Payment recorded", f"Rent for {inv.billing_period} marked as paid.")
    audit(db, user, "rent.marked_paid", "invoice", inv.id, {"payment": p.id})
    db.commit()
    return invoice_out(inv)


# ---------- payments ----------
def payment_out(p: Payment):
    d = to_dict(p)
    d["billing_period"] = p.invoice.billing_period
    return d


@router.get("/payments")
def list_payments(user: User = Depends(Both), db: Session = Depends(get_db)):
    out = []
    for p in db.query(Payment).order_by(Payment.id.desc()).all():
        l = p.invoice.lease
        if (user.role == "landlord" and l.unit.property.landlord_id == user.id) or \
           (user.role == "tenant" and l.tenant.user_id == user.id):
            out.append(payment_out(p))
    return out


@router.post("/payments/create", status_code=201)
def create_payment(body: PayIn, user: User = Depends(require_roles("tenant")), db: Session = Depends(get_db)):
    t = tenant_row(db, user)
    inv = get_invoice_for(db, user, body.invoice_id)
    refresh_invoices(db)
    if inv.status in ("paid", "cancelled"):
        raise HTTPException(409, f"Invoice is {inv.status}")
    outstanding = round(inv.amount - (inv.amount_paid or 0), 2)
    amount = round(body.amount if body.amount is not None else outstanding, 2)
    if amount > outstanding:
        raise HTTPException(422, "Amount exceeds outstanding balance")
    p = Payment(invoice_id=inv.id, tenant_id=t.id, amount=amount, provider="mockpay",
                provider_reference="pay_" + uuid.uuid4().hex[:20], status="pending")
    db.add(p)
    db.flush()
    audit(db, user, "payment.initiated", "payment", p.id)
    db.commit()
    d = payment_out(p)
    # A real integration returns the provider's hosted checkout URL here.
    d["checkout_url"] = f"https://checkout.mockpay.invalid/{p.provider_reference}"
    return d


def process_event(db: Session, evt: dict) -> dict:
    """Authoritative payment state change, driven only by verified provider events. Idempotent."""
    try:
        eid, etype, ref = str(evt["event_id"]), str(evt["type"]), str(evt["reference"])
    except KeyError:
        raise HTTPException(400, "Malformed event")
    if db.get(ProcessedEvent, eid):
        return {"status": "duplicate"}
    p = db.query(Payment).filter_by(provider_reference=ref).first()
    if not p:
        raise HTTPException(404, "Unknown payment reference")
    inv = p.invoice
    db.add(ProcessedEvent(event_id=eid))
    result = "ignored"
    if etype == "payment.succeeded" and p.status == "pending":
        if abs(float(evt.get("amount", p.amount)) - p.amount) > 0.001:
            p.status = "failed"
            audit(db, None, "payment.amount_mismatch", "payment", p.id)
            result = "amount_mismatch"
        else:
            p.status = "succeeded"
            p.paid_at = utcnow()
            inv.amount_paid = round((inv.amount_paid or 0) + p.amount, 2)
            settle(inv)
            notify(db, inv.lease.tenant.user_id, "payment", "Payment received",
                   f"INR {p.amount:,.2f} received for {inv.billing_period}.")
            notify(db, inv.lease.unit.property.landlord_id, "payment", "Rent payment received",
                   f"{inv.lease.tenant.user.name} paid INR {p.amount:,.2f} ({inv.billing_period}).")
            audit(db, None, "payment.succeeded", "payment", p.id)
            result = "succeeded"
    elif etype == "payment.failed" and p.status == "pending":
        p.status = "failed"
        notify(db, inv.lease.tenant.user_id, "payment", "Payment failed", "Your payment could not be completed.")
        audit(db, None, "payment.failed", "payment", p.id)
        result = "failed"
    elif etype == "payment.refunded" and p.status == "succeeded":
        p.status = "refunded"
        inv.amount_paid = round(max((inv.amount_paid or 0) - p.amount, 0), 2)
        settle(inv)
        audit(db, None, "payment.refunded", "payment", p.id)
        result = "refunded"
    try:
        db.commit()
    except IntegrityError:  # concurrent duplicate delivery
        db.rollback()
        return {"status": "duplicate"}
    return {"status": result}


@router.post("/payments/webhook")
async def webhook(request: Request, x_signature: str = Header(default=""), db: Session = Depends(get_db)):
    raw = await request.body()
    expected = hmac.new(settings.WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_signature):
        raise HTTPException(401, "Invalid signature")
    try:
        evt = json.loads(raw)
        if not isinstance(evt, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Invalid JSON")
    return process_event(db, evt)


def _own_payment(db, user, pid) -> Payment:
    p = db.get(Payment, pid)
    if p:
        l = p.invoice.lease
        if (user.role == "landlord" and l.unit.property.landlord_id == user.id) or \
           (user.role == "tenant" and l.tenant.user_id == user.id):
            return p
    raise HTTPException(404, "Payment not found")


@router.post("/payments/{pid}/simulate")
def simulate(pid: int, body: SimulateIn, user: User = Depends(Both), db: Session = Depends(get_db)):
    """DEV ONLY: emulates the provider sending a webhook for this payment."""
    if not settings.DEBUG:
        raise HTTPException(404, "Not found")
    p = _own_payment(db, user, pid)
    etype = "payment.succeeded" if body.outcome == "success" else "payment.failed"
    return process_event(db, {"event_id": "evt_" + uuid.uuid4().hex, "type": etype,
                              "reference": p.provider_reference, "amount": p.amount})


@router.post("/payments/{pid}/refund")
def refund(pid: int, user: User = Depends(Landlord), db: Session = Depends(get_db)):
    p = _own_payment(db, user, pid)
    if p.status != "succeeded":
        raise HTTPException(409, "Only succeeded payments can be refunded")
    # Real integration: call the provider's refund API; the refund webhook then confirms it.
    audit(db, user, "payment.refund_requested", "payment", p.id)
    return process_event(db, {"event_id": "evt_" + uuid.uuid4().hex, "type": "payment.refunded",
                              "reference": p.provider_reference})


@router.get("/payments/{pid}/receipt")
def receipt(pid: int, user: User = Depends(Both), db: Session = Depends(get_db)):
    p = _own_payment(db, user, pid)
    if p.status != "succeeded":
        raise HTTPException(409, "Receipt available only for successful payments")
    inv, l = p.invoice, p.invoice.lease
    data = make_pdf("Rent Payment Receipt", [
        f"Receipt No: RCT-{p.id:06d}", f"Date: {p.paid_at}", f"Tenant: {l.tenant.user.name}",
        f"Property: {l.unit.property.name}   Unit: {l.unit.unit_number}",
        f"Billing period: {inv.billing_period}", f"Amount paid: INR {p.amount:,.2f}",
        f"Method: {p.provider}", f"Reference: {p.provider_reference}"])
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="receipt-{p.id}.pdf"'})
