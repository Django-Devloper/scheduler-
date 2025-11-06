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

The repository now includes a single FastAPI application that exposes the public and admin APIs described in the architecture
spec. The service runs entirely in memory, making it easy to experiment with slot exposure behaviour and the hold → confirm
workflow.

### Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run the API server

```bash
uvicorn app.main:app --reload
```

When the server starts it bootstraps sample data (a location, service, stylist, and availability rule). Use the admin slot
generation endpoint to materialise slots before calling the public APIs.

## Next Steps
- Replace the in-memory store with persistent services (PostgreSQL, Redis, message bus) per the architecture doc.
- Add automated tests covering slot fairness, concurrency, expiry, and RBAC protections.
- Integrate observability (metrics, logs, tracing) and rate limiting within the API gateway.
