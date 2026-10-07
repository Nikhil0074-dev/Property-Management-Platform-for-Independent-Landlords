# API
Interactive docs: run the app and open `/docs`. Endpoints are under `/api`: auth, properties, units, tenants, staff, leases, rent/invoices, payments, maintenance, documents, screening, notifications, dashboard, admin.

Payment webhook: `POST /api/payments/webhook`, header `X-Signature` = HMAC-SHA256(raw body, WEBHOOK_SECRET); body `{"event_id","type":"payment.succeeded|payment.failed|payment.refunded","reference","amount"}`.
