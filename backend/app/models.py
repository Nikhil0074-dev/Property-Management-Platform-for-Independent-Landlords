from datetime import datetime, timezone

from sqlalchemy import (Column, Date, DateTime, ForeignKey, Integer, Numeric,
                        String, Text, UniqueConstraint, event)
from sqlalchemy.orm import relationship

from .database import Base

Money = Numeric(12, 2, asdecimal=False)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False)  # landlord|tenant|maintenance|admin
    status = Column(String(20), default="active", nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # landlord who created tenant/staff
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class PasswordReset(Base):
    __tablename__ = "password_resets"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Integer, default=0)


class Property(Base):
    __tablename__ = "properties"
    id = Column(Integer, primary_key=True)
    landlord_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    name = Column(String(120), nullable=False)
    address = Column(Text, nullable=False)
    property_type = Column(String(20), default="apartment")
    status = Column(String(20), default="active")
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    units = relationship("Unit", back_populates="property")


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (UniqueConstraint("property_id", "unit_number"),)
    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey("properties.id"), index=True, nullable=False)
    unit_number = Column(String(40), nullable=False)
    monthly_rent = Column(Money, nullable=False)
    security_deposit = Column(Money, default=0)
    amenities = Column(Text, default="")
    status = Column(String(20), default="vacant")  # vacant|occupied|maintenance
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    property = relationship("Property", back_populates="units")


class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    landlord_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    phone = Column(String(30), default="")
    status = Column(String(20), default="active")
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    user = relationship("User", foreign_keys=[user_id])


class Lease(Base):
    __tablename__ = "leases"
    id = Column(Integer, primary_key=True)
    unit_id = Column(Integer, ForeignKey("units.id"), index=True, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), index=True, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    monthly_rent = Column(Money, nullable=False)
    deposit = Column(Money, default=0)
    due_day = Column(Integer, default=5)
    late_fee_policy = Column(Text, default="")
    maintenance_terms = Column(Text, default="")
    additional_terms = Column(Text, default="")
    # draft|sent|tenant_signed|active|expiring|expired|renewed
    status = Column(String(20), default="draft")
    document_id = Column(Integer, nullable=True)
    esign_provider = Column(String(40), default="mock-esign")
    esign_envelope_id = Column(String(64), nullable=True)
    tenant_signed_at = Column(DateTime, nullable=True)
    landlord_signed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    unit = relationship("Unit")
    tenant = relationship("Tenant")


class RentInvoice(Base):
    __tablename__ = "rent_invoices"
    __table_args__ = (UniqueConstraint("lease_id", "billing_period"),)
    id = Column(Integer, primary_key=True)
    lease_id = Column(Integer, ForeignKey("leases.id"), index=True, nullable=False)
    billing_period = Column(String(7), nullable=False)  # YYYY-MM
    amount = Column(Money, nullable=False)
    amount_paid = Column(Money, default=0)
    due_date = Column(Date, nullable=False)
    status = Column(String(20), default="pending")  # pending|paid|partially_paid|overdue|cancelled
    created_at = Column(DateTime, default=utcnow)
    lease = relationship("Lease")


class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("rent_invoices.id"), index=True, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    amount = Column(Money, nullable=False)
    provider = Column(String(40), default="mockpay")
    provider_reference = Column(String(64), unique=True, nullable=False)
    status = Column(String(20), default="pending")  # pending|succeeded|failed|refunded
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    invoice = relationship("RentInvoice")


class ProcessedEvent(Base):
    __tablename__ = "processed_events"
    event_id = Column(String(80), primary_key=True)
    created_at = Column(DateTime, default=utcnow)


class Ticket(Base):
    __tablename__ = "maintenance_tickets"
    id = Column(Integer, primary_key=True)
    unit_id = Column(Integer, ForeignKey("units.id"), index=True, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    category = Column(String(40), default="other")
    priority = Column(String(20), default="medium")
    status = Column(String(30), default="open")
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    unit = relationship("Unit")


class TicketCost(Base):
    __tablename__ = "maintenance_costs"
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("maintenance_tickets.id"), index=True, nullable=False)
    description = Column(String(200), default="")
    amount = Column(Money, nullable=False)
    cost_type = Column(String(20), default="other")  # labor|parts|other
    created_at = Column(DateTime, default=utcnow)


class TicketComment(Base):
    __tablename__ = "ticket_comments"
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("maintenance_tickets.id"), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    landlord_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    ticket_id = Column(Integer, nullable=True)
    lease_id = Column(Integer, nullable=True)
    document_type = Column(String(40), default="other")
    filename = Column(String(255), default="")
    storage_key = Column(String(120), nullable=False)
    mime_type = Column(String(80), nullable=False)
    size = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)


class ScreeningRequest(Base):
    __tablename__ = "screening_requests"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    landlord_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    provider = Column(String(40), default="mock-screening")
    provider_reference = Column(String(64), nullable=True)
    status = Column(String(20), default="awaiting_consent")  # awaiting_consent|completed|declined
    consent_at = Column(DateTime, nullable=True)
    requested_at = Column(DateTime, default=utcnow)
    completed_at = Column(DateTime, nullable=True)
    result_reference = Column(String(64), nullable=True)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    type = Column(String(40), nullable=False)
    title = Column(String(200), nullable=False)
    message = Column(Text, default="")
    dedupe_key = Column(String(120), nullable=True, index=True)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    actor_id = Column(Integer, nullable=True)
    action = Column(String(80), nullable=False)
    resource_type = Column(String(40), nullable=False)
    resource_id = Column(String(40), nullable=True)
    meta = Column(Text, default="{}")
    created_at = Column(DateTime, default=utcnow)


@event.listens_for(AuditLog, "before_update")
@event.listens_for(AuditLog, "before_delete")
def _audit_immutable(*_):
    raise ValueError("Audit logs are append-only")
