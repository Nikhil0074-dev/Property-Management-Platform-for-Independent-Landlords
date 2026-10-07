# Security notes
- Passwords: PBKDF2-SHA256 (200k iterations, per-user salt). JWT bearer auth (HS256) with configurable expiry.
- Object-level authorization on every resource (landlord owns property > unit > lease; tenants see only their own; staff only assigned tickets). Cross-tenant access returns 404.
- Platform admin manages users and reads audit logs but has no access to leases, documents, payments or screening data.
- Payments: only HMAC-verified webhook events change payment state; events are idempotent (`processed_events`); amounts are verified. No card data is stored.
- Uploads: extension allow-list, magic-byte check, 5 MB limit, random storage keys, authorized downloads, `nosniff`.
- Screening: consent required, only a result reference is stored, access is audited, mock provider only.
- Audit log is append-only at ORM level; also restrict UPDATE/DELETE via DB permissions in production.
- Production checklist: DEBUG=false, strong SECRET_KEY/WEBHOOK_SECRET, HTTPS, Alembic migrations, S3-style storage behind `services/storage.py`, rate limiting, malware scanning, real e-sign/payment/screening providers after legal review.
