# Invitation rewards

New registrations require email verification. A referred user's successful verification grants the inviter 10 credits; approving a public-video promotion claim grants its owner 30 credits. Grants expire 30 days after issuance. Existing active accounts remain active.

## Deployment

1. Back up the database. Use the Cloud API service environment and database connection.
2. Ensure the existing password-recovery migration has run (its persistent rate-limit table is also used for registration verification).
3. From `deploy/cloud-api`, run `PYTHONPATH=. python migrations/20261028_referral_program.py` before restarting the API or any billing workers. The migration adds tables and may be rerun.
4. Configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_SENDER`. Registration returns 503 if mail settings are unavailable. Delivery uses SMTP over TLS, as password recovery does.
5. Deploy the website and API together: the registration response now returns `verification_required: true`; clients must verify before login.
6. Check `/invite/`, `/register/?invite=CODE`, and `/admin/referrals.html`. Verify real SMTP delivery in the deployment environment before enabling public registration.

This change has not been deployed by committing it. Local validation uses SQLite and mocked email delivery; it does not verify production PostgreSQL concurrency or SMTP delivery.

## Current behavior

- Reward credits are allocated before purchased credits, ordered by expiry. Expired available rewards are removed when the wallet is accessed; expired reserved rewards cannot become permanent credits on refund.
- A platform/account-name pair can submit one promotion claim. Administrators must verify account ownership and that the public video contains the applicant's invite code. Rejected claims currently cannot be resubmitted for the same account name.
- QR images are rendered by QuickChart; the browser sends the invitation URL to that service when displaying the image.
- User endpoints: `/auth/register/verify`, `/auth/register/resend`, `/referrals/me`, `/referrals/promotion-claims` under `/api/v1`.
- Admin endpoints: `/admin/referral-promotion-claims` and `/{claim_id}/review` under that path. Approval and reward issuance share one database transaction.
