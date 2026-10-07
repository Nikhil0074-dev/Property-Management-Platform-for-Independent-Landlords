"""Create an admin and demo data:  python -m app.seed"""
import os
from datetime import date, timedelta

from .database import Base, SessionLocal, engine
from .models import Lease, Property, RentInvoice, Tenant, Unit, User
from .security import hash_password


def main():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    if db.query(User).filter_by(email="admin@example.com").first():
        print("Already seeded")
        return
    pw = os.getenv("SEED_PASSWORD", "Password123!")
    admin = User(name="Platform Admin", email="admin@example.com", password_hash=hash_password(pw), role="admin")
    ll = User(name="Demo Landlord", email="landlord@example.com", password_hash=hash_password(pw), role="landlord")
    db.add_all([admin, ll])
    db.flush()
    tu = User(name="Demo Tenant", email="tenant@example.com", password_hash=hash_password(pw), role="tenant", owner_id=ll.id)
    st = User(name="Demo Plumber", email="staff@example.com", password_hash=hash_password(pw), role="maintenance", owner_id=ll.id)
    db.add_all([tu, st])
    db.flush()
    t = Tenant(user_id=tu.id, landlord_id=ll.id, phone="9000000000")
    p = Property(landlord_id=ll.id, name="Sunrise Apartments", address="12 MG Road, Pune", property_type="apartment")
    db.add_all([t, p])
    db.flush()
    u1 = Unit(property_id=p.id, unit_number="A-102", monthly_rent=20000, security_deposit=40000, status="occupied")
    u2 = Unit(property_id=p.id, unit_number="A-103", monthly_rent=18000, security_deposit=36000)
    db.add_all([u1, u2])
    db.flush()
    today = date.today()
    l = Lease(unit_id=u1.id, tenant_id=t.id, start_date=today - timedelta(days=60), end_date=today + timedelta(days=300),
              monthly_rent=20000, deposit=40000, due_day=5, status="active")
    db.add(l)
    db.flush()
    db.add(RentInvoice(lease_id=l.id, billing_period=today.strftime("%Y-%m"), amount=20000,
                       due_date=today.replace(day=5), status="pending"))
    db.commit()
    print(f"Seeded. Logins (password {pw}): admin@example.com, landlord@example.com, tenant@example.com, staff@example.com")


if __name__ == "__main__":
    main()
