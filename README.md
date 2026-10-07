# Property Management Platform for Independent Landlords

FastAPI + SQLAlchemy backend with a dependency-free web UI served by the backend. SQLite by default; PostgreSQL via `DATABASE_URL`.

## Features
Roles (landlord, tenant, maintenance staff, platform admin) with object-level authorization; properties and units; tenant invitations; lease lifecycle (draft, sent, tenant signed, active, expiring, expired/renewed) with mock e-sign and PDF generation; rent invoices (single/batch), partial payments, overdue handling, manual "mark paid"; payments driven by signed idempotent webhooks, PDF receipts, refunds; maintenance tickets (priority, assignment, status workflow, comments, attachments, cost tracking); secure document upload/download; consent-based mock tenant screening; notifications and reminders; dashboards for every role; append-only audit log.

## Run locally
```bash
cd backend
python -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m app.seed                                   # optional demo data
uvicorn app.main:app --reload
```
Open http://localhost:8000 (API docs at /docs). Demo logins (password `Password123!`): landlord@example.com, tenant@example.com, staff@example.com, admin@example.com.

## Tests
```bash
cd backend && python -m pytest -q
```

## Docker (PostgreSQL)
```bash
docker compose -f infrastructure/docker-compose.yml up --build
```