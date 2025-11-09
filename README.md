# Hair Stylist Scheduler

A production-grade scheduling platform for a multi-location hair stylist brand. The system is built around microservices that manage availability, slots, bookings, and notifications while exposing both public (User) and private (Admin) APIs.

## Documentation
- [Architecture Overview](docs/architecture.md)
- [OpenAPI Specification](api/openapi.yaml)

## Key Features
- Deterministic slot exposure that surfaces 2–5 randomized times per request while preserving inventory.
- Hold → confirm booking workflow with idempotent writes, transactional concurrency control, and sticky exposure caching.
- Admin tooling for defining availability rules, generating slot instances, and managing bookings.

## Getting Started

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

On startup the API will create the `scheduler` schema (if needed) and run the DDL for `locations`, `services`, `stylists`,
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
VALUES ('00000000-0000-0000-0000-000000000010', '00000000-0000-0000-0000-000000000001', 'Downtown Studio', 'Asia/Dubai')
ON CONFLICT DO NOTHING;
```

After creating a location you can POST availability rules and generate slots via the admin API just as in production.

### Explore the API documentation

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Raw OpenAPI document: http://localhost:8000/openapi.yaml

### Launch the browser-based API console

The repository includes a zero-build HTML console under `frontend/` that lets you exercise
both the public and admin endpoints without writing scripts. Serve the directory with any
static file host (for example Python's built-in web server) and open it in your browser:

```bash
cd frontend
python -m http.server 9000
```

Navigate to http://localhost:9000, set the API base URL (e.g. `http://localhost:8000`), and
start calling endpoints. Form inputs map directly to the FastAPI request schema, and responses
are rendered as formatted JSON for easy inspection.

## Next Steps
- Add Redis-backed idempotency caches, hold TTL tracking, and exposure stickiness per the architecture doc.
- Add automated tests covering slot fairness, concurrency, expiry, and RBAC protections.
- Integrate observability (metrics, logs, tracing) and rate limiting within the API gateway.
