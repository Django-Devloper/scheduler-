# Hair Stylist Scheduler

A production-grade scheduling platform for a multi-location hair stylist brand. The system is built around microservices that manage availability, slots, bookings, and notifications while exposing both public (User) and private (Admin) APIs.

## Documentation
- [Architecture Overview](docs/architecture.md)
- [OpenAPI Specification](api/openapi.yaml)

## Key Features
- Deterministic slot exposure that surfaces 2–5 randomized times per request while preserving inventory.
- Hold → confirm booking workflow with idempotent writes, transactional concurrency control, and sticky exposure caching.
- Admin tooling for defining availability rules, generating slot instances, and managing bookings.

## Next Steps
- Implement service scaffolding (Availability, Slot, Booking, Auth) with shared libraries for UUIDv7, idempotency, and tracing.
- Add automated tests covering slot fairness, concurrency, expiry, and RBAC protections.
- Integrate observability (metrics, logs, tracing) and rate limiting within the API gateway.
