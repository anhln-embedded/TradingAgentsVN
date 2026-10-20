# TradingAgentsVN SaaS

The dashboard now requires an individual login. `superadmin` is the highest role;
public registration always creates `user`. Role changes are not accepted from
the browser. Server-side checks protect configuration, Git operations, user
administration, subscription assignment, quota adjustments and audit records.

## Runtime architecture

- FastAPI authenticates requests, authorizes ownership and serves snapshots/SSE.
- PostgreSQL persists accounts, hashed sessions, monthly periods, usage ledger,
  jobs, events, current snapshots and administrative audit records.
- `python -m vnapp.worker` claims queued jobs with database row locks and
  `SKIP LOCKED`. The database is the durable queue; there is no separate broker
  or API-to-broker dual write to lose jobs. Redis can be added later for wake-up
  notifications without becoming the source of truth.
- Each analysis runs in a fresh spawned process with its own report, memory,
  cache and checkpoint directories. API restarts do not kill worker processes.
- The same account on another device discovers the latest job from
  `/api/workspace`, restores its snapshot and subscribes after its event sequence.
  Other accounts cannot subscribe to or read that job. Superadmin can inspect
  a job by ID for support; its normal dashboard still shows its own workspace.
- Concurrent analyses scale by running more worker instances. Each account is
  limited to one queued/running analysis initially. Viewing is independent of
  analysis concurrency.

## Local Windows development

Use the existing Python environment and install `web/requirements.txt`.
SQLite is supported for development and tests only; production requires PostgreSQL.
Runtime files default to `saas/` and `tenants/` under `TA_DATA_DIR` (the repository
root when unset). These paths are ignored by Git.

Create the initial operator account; the password is entered without echo:

```powershell
.\.venv\Scripts\python.exe tools/manage_accounts.py bootstrap --email superadmin
```

Start the API and worker as separate processes/terminals, with the same
`TA_DATABASE_URL`, `TA_DATA_DIR` and `TA_RUNTIME_ENV_FILE` if configured:

```powershell
.\.venv\Scripts\python.exe run_web.py
.\.venv\Scripts\python.exe -m vnapp.worker
```

Open `http://127.0.0.1:8000/login`. Sign in as superadmin and open
**Quản trị người dùng**. Create/register users and assign each a monthly plan.
The superadmin has unlimited analyses by default without needing an assigned plan.
There is no default password, no first-visitor admin claim, and no shared Basic Auth.
Old `TA_WEB_USERNAME`/`TA_WEB_PASSWORD` no longer grant access.

Alternative initial setup: configure `TA_BOOTSTRAP_ADMIN_EMAIL` and
`TA_BOOTSTRAP_ADMIN_PASSWORD` in the server environment. Bootstrap never resets an
existing superadmin password. Remove the bootstrap password after first setup.
Operator recovery:

```powershell
.\.venv\Scripts\python.exe tools/manage_accounts.py reset-password --email superadmin
```

## Production

Copy `deploy/.env.example` to `deploy/.env`; fill in the database password,
bootstrap password, AI credentials and the public HTTPS origin. Use a random
hexadecimal database password so it is also safe in the database URL. Keep all
secrets outside version control. Terminate HTTPS at the reverse proxy; production
session cookies always use Secure, HttpOnly and SameSite=Strict.

Generate the SSI token-encryption key once (do not commit the printed value):

```text
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set these deployment secrets, replacing the domain with the real public host:

```text
TA_PUBLIC_ORIGIN=https://stocks.example.com
SSI_MCP_REDIRECT_URI=https://stocks.example.com/api/integrations/ssi/callback
SSI_MCP_TOKEN_ENCRYPTION_KEY=<generated Fernet key>
```

After HTTPS and the containers are healthy, sign in as superadmin, open
**Tài khoản**, and choose **Đăng nhập bằng tài khoản SSI**. This creates one
read-only `market-data:read` connection for the server. Ordinary users neither
receive the SSI token nor sign in to SSI. The encrypted token is stored in the
shared `app_data` volume so the API and worker containers use the same source.
Leave `SSI_MCP_ACCESS_TOKEN` empty in production.

```text
docker compose --env-file deploy/.env -f deploy/compose.yaml up --build -d
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --scale worker=2
```

Compose starts PostgreSQL, initializes schema version 1, then starts the API and
worker. The database is not exposed on a host port. API and workers share `/data`
for configuration and reports. Multiple hosts will require a shared artifact
store; this initial Compose topology assumes one Docker host. The schema is a
new namespace (`saas_*`); future schema changes require explicit migrations.

Back up **both** PostgreSQL and the `app_data` volume. Do not treat Redis or worker
memory as durable storage. Maintain an event/report retention policy before
large public deployment. Configure reverse-proxy buffering off and streaming
timeouts above the SSE heartbeat interval. Configure proxy trust explicitly if
using forwarded client IPs; never trust arbitrary X-Forwarded-For headers.

## Monthly subscriptions and usage

- Superadmin assigns a named monthly plan and allowance, with a required reason.
- A cycle runs from the assignment timestamp to the same day of the next calendar
  month, clamped to the last day where needed. No rollover of unused analyses.
- Assignment starts a new cycle immediately; it is blocked while that user's job
  is queued/running. It is not a scheduled future renewal.
- Submission reserves one analysis in the same transaction that enqueues the job.
  Repeating an identical request key returns the existing job.
- Successful completion consumes the reservation. Failure refunds it exactly once.
  Refresh, reconnect, replay and reading reports never consume another analysis.
- Adjustments change the allowance in the current cycle and require a reason and
  an idempotency key. The allowance cannot be reduced below used + reserved.
- Expired accounts retain report access but cannot start new analyses.
- Disabling an account revokes its sessions and prevents access/new jobs; it does
  not cancel work that has already been queued or started.
- Payment integration is intentionally deferred at the owner's request.
  `TA_PAYMENT_PROVIDER` is reserved and left blank. No automatic paid renewal or
  payment gateway is connected; assignment is manual and there is no
  payment-success endpoint that can grant allowances from a browser request.

## Failure behavior

Workers heartbeat every five seconds; another worker marks jobs with heartbeats
older than 90 seconds failed and refunds them. An execution timeout defaults to
1,800 seconds (`TA_JOB_TIMEOUT`). Interrupted jobs are not automatically rerun,
because an external LLM call may already have incurred cost. Users may submit a
new job after refund. Job tokens prevent late results from settling a failed job.
Existing core checkpoints remain isolated, but automatic continuation after a
worker/machine crash is not implemented. Queued jobs survive API/worker restarts.

Legacy reports remain on disk; they are not automatically assigned to new users
or exposed through the new history API. A reviewed migration is needed to attach
them to an owner. Existing in-memory runs cannot migrate while in flight.

## Verification

```text
python -m unittest tests_vn.test_saas tests_vn.test_runtime
node tests/test_dashboard_summary.cjs
node tests/test_dashboard_resume.cjs
node tests/test_account_access.cjs
```

The tests cover account isolation, configuration/admin permissions, CSRF, disabled
accounts, quota races, idempotency, single claims, refund/settlement, private report
paths, snapshot/replay and API restarts without any paid LLM calls. PostgreSQL
locking and container startup still need an integration check in the deployment
environment; local SQLite tests do not substitute for PostgreSQL load testing.
