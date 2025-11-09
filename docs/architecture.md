# General Scheduler Architecture

## Overview
The General Scheduler is a production-grade booking platform that can be embedded into any product domain. It exposes separate User and Admin APIs while enforcing strong consistency, deterministic slot exposure, and robust booking orchestration.

## Service Topology
- **API Gateway / Edge** – Authenticates traffic, applies rate limits, and routes to public (User) and private (Admin) backends.
- **Auth Service** – Issues and validates JWTs. Supports RBAC roles: `admin`, `manager`, `scheduler`, `frontdesk`, `user`.
- **Availability Service** – Manages working hours, exceptions, templates, and materializes slot instances.
- **Slot Service** – Reads slot instances, calculates capacity, and applies the deterministic exposure algorithm (1–3 person scoped or 3–5 location scoped slots) per user request.
- **Booking Service** – Runs the hold → confirm workflow with idempotent mutations, transactional concurrency control, and post-booking events.
- **Notification Service** *(optional)* – Emits SMS/Email/WhatsApp reminders from booking lifecycle events.
- **Audit / Analytics** *(optional)* – Streams events for BI dashboards and compliance.

Each service is containerized with `/healthz` and `/readyz` probes, instrumented with metrics (`exposed_slots_count`, `hold_to_confirm_ms`, etc.), and participates in distributed tracing via the `X-Request-Id` header.

## Data Stores
- **PostgreSQL** – Authoritative store for locations, people, availability rules, slot instances, and bookings. All timestamps persist in UTC.
- **Redis** – Holds temporary booking holds (TTL 10 minutes), rate limit state, and sticky slot exposure caches (7 minutes).
- **Message Bus (Kafka/SNS/SQS)** – Optional backbone for async notifications (`booking.created`, `booking.confirmed`, `booking.cancelled`).

### Relational Schema
```sql
CREATE TABLE locations (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  timezone TEXT NOT NULL DEFAULT 'Asia/Dubai'
);

CREATE TABLE people (
  id UUID PRIMARY KEY,
  location_id UUID REFERENCES locations(id),
  name TEXT NOT NULL,
  skills TEXT[],
  active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE availability_rules (
  id UUID PRIMARY KEY,
  location_id UUID REFERENCES locations(id),
  person_id UUID NULL REFERENCES people(id),
  rule_kind TEXT NOT NULL CHECK (rule_kind IN ('WEEKLY','DATE_RANGE','EXCEPTION')),
  days_of_week SMALLINT[],
  start_time TIME NOT NULL,
  end_time TIME NOT NULL,
  slot_capacity SMALLINT NOT NULL DEFAULT 1,
  slot_granularity_minutes SMALLINT NOT NULL DEFAULT 15,
  slot_duration_minutes SMALLINT NOT NULL DEFAULT 30,
  valid_from DATE,
  valid_to DATE,
  is_closed BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE(location_id, person_id, rule_kind, days_of_week, start_time, end_time, valid_from, valid_to)
);

CREATE TABLE slot_instances (
  id UUID PRIMARY KEY,
  location_id UUID REFERENCES locations(id),
  person_id UUID NULL REFERENCES people(id),
  date DATE NOT NULL,
  start_at TIMESTAMPTZ NOT NULL,
  end_at TIMESTAMPTZ NOT NULL,
  capacity SMALLINT NOT NULL,
  booked SMALLINT NOT NULL DEFAULT 0,
  hold SMALLINT NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('open','partial','full','blocked')),
  UNIQUE(location_id, person_id, start_at)
);

CREATE TABLE bookings (
  id UUID PRIMARY KEY,
  slot_id UUID NOT NULL REFERENCES slot_instances(id),
  user_id UUID NULL,
  customer_name TEXT NOT NULL,
  customer_phone TEXT NOT NULL,
  customer_email TEXT,
  notes TEXT,
  status TEXT NOT NULL CHECK (status IN ('held','confirmed','cancelled','expired')),
  hold_expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  confirmed_at TIMESTAMPTZ,
  cancelled_at TIMESTAMPTZ,
  UNIQUE(slot_id, customer_phone, created_at::date)
);
```

## Time & Timezone Strategy
- API inputs accept an optional `timezone`; defaults derive from the associated location.
- Internally the platform persists UTC timestamps. Rendering converts to user or location timezones via IANA TZ database.
- Daylight savings transitions rely on Postgres/Redis TZ support; slot generation uses timezone-aware calendars.

## Identifiers & Idempotency
- All resources use UUIDv7 identifiers.
- Mutating endpoints require an `Idempotency-Key` header. Responses are cached for 24 hours keyed by `(key, actor, payload hash)` to guard against duplicate holds or confirmations.

## Slot Exposure (Person 1–3, Location 3–5 Randomized Slots)
1. Compute the deterministic seed: `hash(user_or_session_id + date + (person_id or 'all') + context_window)`. Rotate the salt hourly to prevent starvation.
2. Shuffle eligible slots (`status IN ('open','partial')` and `capacity > booked + hold`).
3. Determine `k` based on context:
   - Person scoped queries expose between 1 and 3 slots (inclusive), capped by availability.
   - Location scoped queries expose between 3 and 5 slots (inclusive), capped by availability.
   - Counts are chosen deterministically via a seeded RNG so the same user/session sees consistent sizes within the cache TTL.
4. Enforce day-part fairness across `{morning, afternoon, evening}` buckets when slots exist.
5. Cache exposed slot IDs in Redis for 7 minutes (`expose:{user}:{date}:{person}`) to ensure consistent UX.
6. Return payload: exposed slots, total availability count, `has_more` flag.

Guardrails:
- Ensure expired holds refresh counts before exposure decisions.
- Prevent starvation by rotating the seed hourly.
- Support experiment header `X-Exposure-Variant` to toggle rules.

### Pseudocode
```python
slots = db.find_available_slots(...)
seed = hash(user_or_session_id + date + (person_id or 'all') + context_window)
shuffled = deterministic_shuffle(slots, seed)

AM = [s for s in shuffled if 6 <= s.local_start.hour < 12]
PM = [s for s in shuffled if 12 <= s.local_start.hour < 17]
EV = [s for s in shuffled if 17 <= s.local_start.hour < 22]

window = range(1, min(len(slots), 3) + 1) if person_id else range(3, min(len(slots), 5) + 1)
k = rng_choice_from_seed(seed, window)

pick = []
for bucket in (AM, PM, EV):
    if bucket and len(pick) < k:
        pick.append(bucket.pop(0))

rest = [s for s in shuffled if s not in pick]
pick += rest[: max(0, k - len(pick))]

cache.set(key, pick_ids, ttl=420)
return expose(pick, total=len(slots), has_more=len(slots) > len(pick))
```

## Booking Flow (Hold → Confirm)
1. User selects an exposed slot and posts `/bookings` with `Idempotency-Key`.
2. Booking Service starts a transaction, locks the `slot_instances` row (`SELECT ... FOR UPDATE`), verifies `capacity - (booked + hold) > 0`, increments `hold`, and creates `booking(status='held')` with `hold_expires_at = now + 10 min`.
3. Optional payment step triggers `/bookings/{id}/confirm` which decrements `hold`, increments `booked`, and sets `status='confirmed'`.
4. Background worker expires holds past `hold_expires_at`, restoring capacity and marking bookings `expired`.
5. Admin cancel/reschedule flows adjust counts and optionally trigger notifications.

Concurrency is enforced through transactional checks; on contention the API returns `409 CONFLICT` with `SLOT_FULL` errors.

## APIs
Two API surfaces exist behind the gateway.

### User API (`/v1`)
- `GET /dates` – Future dates with availability counts.
- `GET /slots` – Exposed slots (1–3 person scoped or 3–5 location scoped) plus totals and `has_more`.
- `POST /bookings` – Places a hold; requires `Idempotency-Key`.
- `POST /bookings/{id}/confirm` – Confirms a held booking.

### Admin API (`/admin/v1`)
- `POST /locations` / `GET /locations` – Manage scheduler locations and their timezones.
- `POST /people` / `GET /people` – Manage schedulable people tied to locations.
- `POST /availabilities` – Define weekly/date range/exception rules.
- `POST /slots/generate` – Materialize slot instances for a date range.
- `GET /bookings` – Filterable list of bookings.
- `PATCH /bookings/{id}` – Cancel or reschedule bookings.

Admin endpoints require JWT roles `admin`, `manager`, or `frontdesk`.

## Operational Workflows
- **Cron Jobs** – Hourly slot generation, minutely hold expiration.
- **Rate Limits** – User GETs 60/min; POST bookings 10/min per IP/session.
- **PII Security** – Encrypt phone/email (pgcrypto or KMS). Mask PII in logs.
- **Metrics & Tracing** – Record booking funnel metrics, conversion, conflicts. Propagate `X-Request-Id` for distributed tracing.

## Test Matrix
- Slot exposure fairness & determinism.
- Concurrency: 50 parallel holds limited by capacity.
- Idempotency: repeated POST with same key yields same booking.
- Expiry: held bookings auto-expire and restore capacity.
- Admin validation: overlapping rules, slot generation idempotency.
- Security: role-based gating, PII redaction.

## Future Enhancements
Membership programs, deposits/no-show penalties, waitlists with auto-fill, 2-way calendar sync, richer notification channels, travel time buffers for team members, and integrated payments are planned for later phases.
