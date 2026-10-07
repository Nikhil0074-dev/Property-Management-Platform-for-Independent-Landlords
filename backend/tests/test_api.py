import hashlib
import hmac
import json
from datetime import date, timedelta

from app.config import settings
from .conftest import H


def reg(client, email, name="Landlord"):
    r = client.post("/api/auth/register", json={"name": name, "email": email, "password": "Secret123!"})
    assert r.status_code == 201, r.text
    return r.json()["token"]


def login(client, email, pw):
    r = client.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def setup_world(client, tag):
    """landlord + property + unit + tenant + staff + active lease. Returns dict."""
    lt = reg(client, f"ll-{tag}@x.com")
    p = client.post("/api/properties", json={"name": "Sunrise", "address": "Pune"}, headers=H(lt))
    assert p.status_code == 201, p.text
    u = client.post("/api/units", json={"property_id": p.json()["id"], "unit_number": "A-102", "monthly_rent": 20000,
                                        "security_deposit": 40000}, headers=H(lt))
    assert u.status_code == 201, u.text
    t = client.post("/api/tenants", json={"name": "John", "email": f"t-{tag}@x.com"}, headers=H(lt))
    assert t.status_code == 201, t.text
    tt = login(client, f"t-{tag}@x.com", t.json()["temp_password"])
    s = client.post("/api/staff", json={"name": "Raj", "email": f"s-{tag}@x.com"}, headers=H(lt))
    assert s.status_code == 201, s.text
    st = login(client, f"s-{tag}@x.com", s.json()["temp_password"])
    today = date.today()
    l = client.post("/api/leases", json={"unit_id": u.json()["id"], "tenant_id": t.json()["id"],
                                         "start_date": str(today), "end_date": str(today + timedelta(days=365)),
                                         "monthly_rent": 20000, "deposit": 40000}, headers=H(lt))
    assert l.status_code == 201, l.text
    lid = l.json()["id"]
    assert client.post(f"/api/leases/{lid}/send", headers=H(lt)).status_code == 200
    assert client.post(f"/api/leases/{lid}/sign", headers=H(lt)).status_code == 409  # tenant must sign first
    assert client.post(f"/api/leases/{lid}/sign", headers=H(tt)).json()["status"] == "tenant_signed"
    done = client.post(f"/api/leases/{lid}/sign", headers=H(lt)).json()
    assert done["status"] == "active" and done["document_id"]
    return dict(lt=lt, tt=tt, st=st, lease=lid, tenant=t.json()["id"], staff=s.json()["id"], unit=u.json()["id"])


def sign_event(evt):
    raw = json.dumps(evt).encode()
    sig = hmac.new(settings.WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Signature": sig, "Content-Type": "application/json"}


def test_auth_and_reset(client):
    reg(client, "a@x.com")
    assert client.post("/api/auth/register", json={"name": "A", "email": "a@x.com", "password": "Secret123!"}).status_code == 409
    assert client.post("/api/auth/login", json={"email": "a@x.com", "password": "wrong"}).status_code == 401
    assert client.get("/api/properties").status_code == 401
    tok = client.post("/api/auth/password-reset/request", json={"email": "a@x.com"}).json()["dev_token"]
    assert client.post("/api/auth/password-reset/confirm", json={"token": tok, "new_password": "NewSecret123"}).status_code == 200
    assert client.post("/api/auth/password-reset/confirm", json={"token": tok, "new_password": "NewSecret123"}).status_code == 400
    login(client, "a@x.com", "NewSecret123")


def test_rent_payment_flow_and_idempotency(client):
    w = setup_world(client, "pay")
    period = date.today().strftime("%Y-%m")
    inv = client.post("/api/rent/invoices", json={"lease_id": w["lease"], "billing_period": period}, headers=H(w["lt"]))
    assert inv.status_code == 201, inv.text
    iid = inv.json()["id"]
    assert client.post("/api/rent/invoices", json={"lease_id": w["lease"], "billing_period": period}, headers=H(w["lt"])).status_code == 409
    assert client.post("/api/rent/invoices", json={"lease_id": w["lease"], "billing_period": "2026-13"}, headers=H(w["lt"])).status_code == 422
    # partial payment via verified webhook
    pay = client.post("/api/payments/create", json={"invoice_id": iid, "amount": 5000}, headers=H(w["tt"]))
    assert pay.status_code == 201, pay.text
    ref, pid = pay.json()["provider_reference"], pay.json()["id"]
    assert client.get(f"/api/payments/{pid}/receipt", headers=H(w["tt"])).status_code == 409
    evt = {"event_id": "evt_1", "type": "payment.succeeded", "reference": ref, "amount": 5000}
    raw, hdr = sign_event(evt)
    assert client.post("/api/payments/webhook", content=raw, headers={"X-Signature": "bad"}).status_code == 401
    assert client.post("/api/payments/webhook", content=raw, headers=hdr).json()["status"] == "succeeded"
    assert client.post("/api/payments/webhook", content=raw, headers=hdr).json()["status"] == "duplicate"
    inv = client.get(f"/api/rent/invoices/{iid}", headers=H(w["tt"])).json()
    assert inv["amount_paid"] == 5000 and inv["outstanding"] == 15000
    assert client.get(f"/api/payments/{pid}/receipt", headers=H(w["tt"])).headers["content-type"] == "application/pdf"
    # pay the rest through the dev simulator
    pay2 = client.post("/api/payments/create", json={"invoice_id": iid}, headers=H(w["tt"])).json()
    assert pay2["amount"] == 15000
    assert client.post(f"/api/payments/{pay2['id']}/simulate", json={"outcome": "success"}, headers=H(w["tt"])).json()["status"] == "succeeded"
    assert client.get(f"/api/rent/invoices/{iid}", headers=H(w["lt"])).json()["status"] == "paid"
    assert client.post("/api/payments/create", json={"invoice_id": iid}, headers=H(w["tt"])).status_code == 409
    # refund
    assert client.post(f"/api/payments/{pay2['id']}/refund", headers=H(w["lt"])).json()["status"] == "refunded"
    assert client.get(f"/api/rent/invoices/{iid}", headers=H(w["lt"])).json()["amount_paid"] == 5000
    # dashboards
    d = client.get("/api/dashboard", headers=H(w["lt"])).json()
    assert d["rent_due"] == 20000 and d["collected"] == 5000 and d["occupancy_rate"] == 100
    assert client.get("/api/dashboard", headers=H(w["tt"])).json()["next_due"]["outstanding"] == 15000
    # batch generation skips existing
    nxt = (date.today().replace(day=1) + timedelta(days=40)).strftime("%Y-%m")
    assert client.post("/api/rent/invoices/generate", json={"billing_period": nxt}, headers=H(w["lt"])).json()["created"] == 1
    assert client.post("/api/rent/invoices/generate", json={"billing_period": nxt}, headers=H(w["lt"])).json()["created"] == 0
    assert client.post("/api/notifications/run-reminders", headers=H(w["lt"])).status_code == 200


def test_cross_tenant_and_landlord_isolation(client):
    a = setup_world(client, "iso1")
    b = setup_world(client, "iso2")
    assert client.get(f"/api/leases/{a['lease']}", headers=H(b["tt"])).status_code == 404
    assert client.get(f"/api/leases/{a['lease']}", headers=H(b["lt"])).status_code == 404
    assert client.get(f"/api/leases/{a['lease']}/pdf", headers=H(b["tt"])).status_code == 404
    assert client.get(f"/api/leases/{a['lease']}", headers=H(a["tt"])).status_code == 200
    assert client.get("/api/properties", headers=H(a["tt"])).status_code == 403
    assert client.get("/api/admin/users", headers=H(a["lt"])).status_code == 403
    assert client.get("/api/leases", headers=H(b["tt"])).json()[0]["id"] == b["lease"]
    assert client.post("/api/screening", json={"tenant_id": a["tenant"]}, headers=H(b["lt"])).status_code == 404


def test_maintenance_documents_screening(client):
    w = setup_world(client, "mnt")
    t = client.post("/api/maintenance", json={"title": "Sink leaking", "category": "plumbing", "priority": "high"}, headers=H(w["tt"]))
    assert t.status_code == 201, t.text
    tid = t.json()["id"]
    assert "safety_notice" in t.json()
    assert client.patch(f"/api/maintenance/{tid}", json={"priority": "low"}, headers=H(w["tt"])).status_code == 403
    assert client.get(f"/api/maintenance/{tid}", headers=H(w["st"])).status_code == 404  # not assigned yet
    r = client.patch(f"/api/maintenance/{tid}", json={"assigned_to": w["staff"]}, headers=H(w["lt"]))
    assert r.status_code == 200 and r.json()["status"] == "assigned"
    assert client.patch(f"/api/maintenance/{tid}", json={"status": "resolved"}, headers=H(w["st"])).status_code == 409
    assert client.patch(f"/api/maintenance/{tid}", json={"status": "in_progress"}, headers=H(w["st"])).status_code == 200
    assert client.patch(f"/api/maintenance/{tid}", json={"status": "closed"}, headers=H(w["st"])).status_code == 403
    # upload completion photo
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    up = client.post("/api/documents", files={"file": ("a.png", png, "image/png")}, data={"ticket_id": str(tid)}, headers=H(w["st"]))
    assert up.status_code == 201, up.text
    assert client.post("/api/documents", files={"file": ("a.exe", b"MZ", "application/octet-stream")}, headers=H(w["st"])).status_code == 415
    assert client.post("/api/documents", files={"file": ("a.png", b"notpng", "image/png")}, headers=H(w["st"])).status_code == 415
    assert client.get(f"/api/documents/{up.json()['id']}/download", headers=H(w["tt"])).content == png
    other = setup_world(client, "mnt2")
    assert client.get(f"/api/documents/{up.json()['id']}/download", headers=H(other["tt"])).status_code == 404
    assert client.patch(f"/api/maintenance/{tid}", json={"status": "resolved"}, headers=H(w["st"])).status_code == 200
    assert client.patch(f"/api/maintenance/{tid}", json={"status": "closed"}, headers=H(w["tt"])).status_code == 200
    assert client.post(f"/api/maintenance/{tid}/comments", json={"body": "Thanks"}, headers=H(w["tt"])).status_code == 201
    assert client.post(f"/api/maintenance/{tid}/costs", json={"amount": 1500, "cost_type": "labor"}, headers=H(w["lt"])).status_code == 201
    assert client.post(f"/api/maintenance/{tid}/costs", json={"amount": 800, "cost_type": "parts"}, headers=H(w["lt"])).status_code == 201
    assert client.get(f"/api/maintenance/{tid}/costs", headers=H(w["lt"])).json()["total"] == 2300
    assert client.post(f"/api/maintenance/{tid}/costs", json={"amount": 5}, headers=H(w["tt"])).status_code == 403
    assert client.get("/api/dashboard", headers=H(w["lt"])).json()["maintenance_cost_by_category"]["plumbing"] == 2300
    # screening with consent
    s = client.post("/api/screening", json={"tenant_id": w["tenant"]}, headers=H(w["lt"])).json()
    assert s["status"] == "awaiting_consent" and not s["result_reference"]
    done = client.post(f"/api/screening/{s['id']}/consent", json={"consent": True}, headers=H(w["tt"])).json()
    assert done["status"] == "completed" and done["result_reference"].startswith("MOCK-")
    assert client.get(f"/api/screening/{s['id']}", headers=H(other["tt"])).status_code == 404
    # notifications & lease pdf
    n = client.get("/api/notifications", headers=H(w["tt"])).json()
    assert n and client.post(f"/api/notifications/{n[0]['id']}/read", headers=H(w["tt"])).json()["read_at"]
    assert client.get(f"/api/leases/{w['lease']}/pdf", headers=H(w["lt"])).content.startswith(b"%PDF")


def test_admin_and_audit(client):
    from app.database import SessionLocal
    from app.models import User
    from app.security import hash_password
    db = SessionLocal()
    db.add(User(name="Admin", email="root@x.com", password_hash=hash_password("AdminPass123"), role="admin"))
    db.commit()
    db.close()
    at = login(client, "root@x.com", "AdminPass123")
    logs = client.get("/api/admin/audit-logs", headers=H(at)).json()
    assert any(l["action"] == "lease.signed" for l in logs)
    users = client.get("/api/admin/users", headers=H(at)).json()
    assert "password_hash" not in users[0]
    victim = next(u for u in users if u["role"] == "landlord")
    assert client.patch(f"/api/admin/users/{victim['id']}", json={"status": "suspended"}, headers=H(at)).status_code == 200
    assert client.post("/api/auth/login", json={"email": victim["email"], "password": "Secret123!"}).status_code == 401
    assert client.get("/api/properties", headers=H(at)).status_code == 403
    assert client.get("/api/dashboard", headers=H(at)).json()["role"] == "admin"
