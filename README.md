# General Scheduler

A production-grade scheduling platform that can be embedded into any application needing appointment style scheduling. The system manages locations, people, availability, slots, and bookings while exposing both public (User) and private (Admin) APIs.

## Documentation
- [Architecture Overview](docs/architecture.md)
- [OpenAPI Specification](api/openapi.yaml)

## Key Features
- Deterministic slot exposure that surfaces 1–3 slots when a specific person is requested and 3–5 when browsing by location, while preserving inventory fairness.
- Hold → confirm booking workflow with idempotent writes, transactional concurrency control, and sticky exposure caching.
- Admin tooling for defining locations, people, availability rules, generating slot instances, and managing bookings for any set of resources.

## Getting Started

### Run with Docker

The repository ships with a production-ready Dockerfile and compose stack. Build and
launch the API together with PostgreSQL by running:

```bash
docker compose up --build
```

The compose file exposes the API on port `8000` and PostgreSQL on `5432`. Update the
database credentials in `.env` (or override `DATABASE_URL` in the compose file) if you
need to customise them for your deployment target.

The FastAPI service now persists availability, slots, and bookings in PostgreSQL under a dedicated `scheduler` schema while
referencing shared `public.auth_user` and `public.biz_entity` tables for user and business metadata. The app automatically
creates the schema and tables at startup, so the only prerequisite is access to a PostgreSQL instance.

### 1. Start PostgreSQL locally

Launch a development database with Docker Compose (feel free to adjust credentials as needed):

```bash
docker compose -f docker-compose.local.yml up -d
```

The compose file provisions a `scheduler` database with the username/password `scheduler/scheduler` and forwards port 5432.

> **Note**
> The application expects the shared tables `public.auth_user` and `public.biz_entity` to exist. For local development you can
> create lightweight stand-ins by running `psql -f sql/local_setup.sql` against your database. Production deployments should
> rely on the real authentication/business tables that already live in the `public` schema.

### 2. Configure environment variables

Copy the example environment file and update it if you customised credentials:

```bash
cp .env.example .env
```

The defaults match the Docker Compose configuration (`postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler`).

> **Troubleshooting:** If you see `asyncpg.exceptions.InvalidPasswordError` during application startup, double-check that your
> database user/password match either the values in `.env` or the individual `POSTGRES_*` overrides. The docker-compose
> service and the built-in defaults both use `scheduler/scheduler`; adjust either the database user or the environment variables
> to keep them aligned.

### 3. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Run the API server

```bash
uvicorn app.main:app --reload --env-file .env
```

On startup the API will create the `scheduler` schema (if needed) and run the DDL for `locations`, `people`,
`availability_rules`, `slot_instances`, `bookings`, and `idempotency_keys`. Use the admin endpoints to load availability rules
and generate slot instances after seeding any required reference data.

### 5. Seed sample scheduler data (optional)

With the database running you can insert placeholder business data and a scheduler location to try the endpoints quickly:

```sql
-- Example: seed via psql
INSERT INTO public.biz_entity (id, name) VALUES ('00000000-0000-0000-0000-000000000001', 'Downtown Studio')
ON CONFLICT DO NOTHING;

INSERT INTO public.auth_user (id, email) VALUES ('00000000-0000-0000-0000-0000000000aa', 'admin@example.com')
ON CONFLICT DO NOTHING;

INSERT INTO scheduler.locations (id, biz_entity_id, name, timezone)
VALUES ('00000000-0000-0000-0000-000000000010', '00000000-0000-0000-0000-000000000001', 'Downtown Workspace', 'Asia/Dubai')
ON CONFLICT DO NOTHING;

INSERT INTO scheduler.people (id, location_id, name)
VALUES ('00000000-0000-0000-0000-000000000020', '00000000-0000-0000-0000-000000000010', 'Alex Example')
ON CONFLICT DO NOTHING;
```

After creating a location you can insert people into `scheduler.people`, define availability rules, and generate slots via the admin API.

### Explore the API documentation

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Raw OpenAPI document: http://localhost:8000/openapi.yaml

## Next Steps
- Add Redis-backed idempotency caches, hold TTL tracking, and exposure stickiness per the architecture doc.
- Add automated tests covering slot fairness, concurrency, expiry, and RBAC protections.
- Integrate observability (metrics, logs, tracing) and rate limiting within the API gateway.
