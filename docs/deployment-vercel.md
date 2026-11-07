# Deploying the Nexus API to Vercel + Neon

Serverless FastAPI (`api/index.py` → `/api/v1`) with a managed Neon
Postgres. Cold starts (1–3s) are expected; there is no always-on process to
keep alive — Vercel scales to zero and wakes on request.

## 1) Provision Neon (once)

1. neon.tech → Create project (Free, no card). Region nearest your runtime.
2. In the dashboard, copy the **pooled** connection string
   (`...neon.tech/...?sslmode=require`). Keep it secret.
3. Set the project to a fixed branch/role so `NEXUS_DATABASE_URL` stays
   stable.

## 2) Run migrations (once, then per release)

From a machine with the pooled URL as `NEXUS_DATABASE_URL` (use your shell
or CI — never commit the URL):

```bash
export NEXUS_DATABASE_URL="postgresql+asyncpg://<user>:<pass>@<host>/<db>?sslmode=require"
cd backend
uv run alembic upgrade head
uv run alembic current
```

The API does not migrate on request; deploys never run Alembic.

## 3) Link the repo in Vercel

1. Vercel → Add New → Project → import `Simonmuchiri001/Nexus-Payroll`.
   Framework: **Other** (Vercel auto-detects `vercel.json` + `api/index.py`).
   Root directory: repository root.
2. Environment variables (Production + Preview), all secret:

   | Name | Value |
   |---|---|
   | `NEXUS_ENVIRONMENT` | `production` |
   | `NEXUS_DATABASE_URL` | Neon pooled URL from step 1 |
   | `NEXUS_JWT_SECRET` | `openssl rand -hex 32` (≥32 chars, not `dev-/test-/change-me`) |
   | `NEXUS_ALLOWED_HOSTS` | `["<your-project>.vercel.app","localhost"]` |
   | `NEXUS_CORS_ORIGINS` | *(optional)* your desktop/web origins |

3. Deploy. First push to `main` builds the Python function and publishes
   `https://<project>.vercel.app`.

## 4) Verify

```bash
curl https://<project>.vercel.app/api/v1/health/ready
# → {"status":"ok","database":true,...}
```

Point the Mac desktop app at
`https://<project>.vercel.app/api/v1` on the login screen (the app asks
for the API URL each sign-in; no rebuild needed).

## Notes

- **Pooling:** on Vercel (`VERCEL` env present) the engine uses `NullPool`
  behind Neon's PgBouncer, so concurrent cold starts do not exhaust compute
  connections. Locally it keeps a small queue.
- **Timeouts:** keep requests under 60s (Vercel `maxDuration`). Payroll
  calculations, exports and migrations must stay short.
- **No cron/background jobs on Vercel.** Use Vercel Cron or an external
  scheduler for recurring work.
- **Rollbacks:** `vercel rollback` or redeploy a previous Git commit.
  Migrations are forward-only; downgrade only in a maintenance window.
- **Root cause check:** `GET /api/v1/health/ready` distinguishes API vs
  DB failures. Neon `database: false` means check `NEXUS_DATABASE_URL`,
  SSL and Neon compute quota.
