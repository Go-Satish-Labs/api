# Database connectivity (Supabase Postgres)

`GET /auth/me` used to answer `500 Internal Server Error` with a 21-byte
`text/plain` body whenever Supabase was unreachable. The real cause was
`psycopg.errors.ConnectionTimeout` inside SQLAlchemy: no `connect_timeout` was
set, so a request hung on a dead pooler for minutes and the ASGI server turned it
into a bare 500. Nothing in that body told you *which* database was broken.

## How it behaves now

| Situation | Response |
| --- | --- |
| Database reachable | normal response, `GET /health` -> `status: healthy` |
| Database down, `DB_SQLITE_FALLBACK=true` | normal response served from `DB_SQLITE_FALLBACK_URL`, `GET /health` -> `mode: sqlite-fallback` |
| Database down, fallback off | `503` with JSON `{"detail", "error", "database": {...}}` instead of `500` + `text/plain` |

Startup never aborts: `app/database.py:init_engine()` probes the DSN once, logs
`[database] ...` lines to stderr, and either swaps in the SQLite fallback or keeps
the broken engine so every DB route answers `503`.

## Settings (Brain/.env)

```
DATABASE_URL=postgresql+psycopg://postgres.<REF>:<url-encoded pw>@<host>:5432/postgres?sslmode=require
DB_CONNECT_TIMEOUT_SECONDS=8    # give up on a new connection after 8 s
DB_POOL_TIMEOUT_SECONDS=8       # give up waiting for a pooled connection after 8 s
DB_POOL_RECYCLE_SECONDS=300     # Supabase kills idle conns; recycle well before that
DB_SQLITE_FALLBACK=true         # dev-only safety net; MUST be false in production
DB_SQLITE_FALLBACK_URL=sqlite:///./analytics_app.db
```

`DB_SQLITE_FALLBACK=true` while Supabase is broken keeps the UI usable, but new
rows land in the local SQLite file and never reach Supabase. Set it to `false` as
soon as the DSN works.

## Fixing a failing DSN

After editing `Brain/.env`, restart uvicorn (`.env` is read at import time) and
confirm with `GET /health/db` — that endpoint re-probes the active engine instead
of reporting cached state.

| Message | Cause / fix |
| --- | --- |
| `FATAL: password authentication failed for user "postgres"` | Wrong database password. Supabase dashboard -> Project Settings -> Database -> Reset database password. URL-encode it in `DATABASE_URL` (`@` -> `%40`, `:` -> `%3A`, `/` -> `%2F`). |
| `FATAL: (ENOTFOUND) tenant/user postgres.<ref> not found` | Connection pooling is disabled for the project. Enable Project Settings -> Database -> Connection pooling, or switch to the direct `db.<ref>.supabase.co` host with user `postgres`. |
| `connection timeout expired` / `TCP BLOCKED` | Project paused (free plan after inactivity), IP restriction, VPN or firewall blocking 5432/6543. |
| `SSL errors` / `certificate` | Keep `?sslmode=require`; drop it only if the provider says otherwise. |
| works once, then hangs later | Idle connections were reaped. Lower `DB_POOL_RECYCLE_SECONDS`, and on Supabase use the **session** pooler (`:5432`) for SQLAlchemy (the transaction pooler rejects the prepared statements / `SET` calls some drivers issue). |

## Notes

- Credentials are only ever printed through `app.database.redact()`, so logs and
  `/health` show `postgres:***@host`, never the password.