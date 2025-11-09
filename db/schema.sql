-- Scheduler schema DDL
-- Creates the scheduler schema and tables for locations, people, availability rules,
-- slot instances, bookings, and idempotency keys. Mirrors the SQLAlchemy models
-- defined in app/models.py.

CREATE SCHEMA IF NOT EXISTS scheduler;

-- Locations represent physical or logical places where people can offer availability.
CREATE TABLE IF NOT EXISTS scheduler.locations (
    id UUID PRIMARY KEY,
    biz_entity_id UUID NULL REFERENCES public.biz_entity(id),
    name VARCHAR(255) NOT NULL,
    timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Dubai',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- People are the resources that can be scheduled at a location.
CREATE TABLE IF NOT EXISTS scheduler.people (
    id UUID PRIMARY KEY,
    location_id UUID NOT NULL REFERENCES scheduler.locations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    skills JSONB NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Availability rules define recurring availability for a person or location.
CREATE TABLE IF NOT EXISTS scheduler.availability_rules (
    id UUID PRIMARY KEY,
    location_id UUID NOT NULL REFERENCES scheduler.locations(id) ON DELETE CASCADE,
    person_id UUID NULL REFERENCES scheduler.people(id) ON DELETE SET NULL,
    rule_kind VARCHAR(32) NOT NULL,
    days_of_week JSONB NULL,
    start_time TIME WITHOUT TIME ZONE NOT NULL,
    end_time TIME WITHOUT TIME ZONE NOT NULL,
    slot_capacity SMALLINT NOT NULL DEFAULT 1 CHECK (slot_capacity > 0),
    slot_granularity_minutes SMALLINT NOT NULL DEFAULT 15 CHECK (slot_granularity_minutes > 0),
    slot_duration_minutes SMALLINT NOT NULL DEFAULT 30,
    valid_from DATE NULL,
    valid_to DATE NULL,
    is_closed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Slot instances are concrete bookable slots created from availability rules.
CREATE TABLE IF NOT EXISTS scheduler.slot_instances (
    id UUID PRIMARY KEY,
    location_id UUID NOT NULL REFERENCES scheduler.locations(id) ON DELETE CASCADE,
    person_id UUID NULL REFERENCES scheduler.people(id) ON DELETE SET NULL,
    date DATE NOT NULL,
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    capacity SMALLINT NOT NULL CHECK (capacity > 0),
    booked SMALLINT NOT NULL DEFAULT 0,
    hold SMALLINT NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_slot_instances_natural UNIQUE (location_id, person_id, start_at)
);

-- Bookings represent reservations made against a slot instance.
CREATE TABLE IF NOT EXISTS scheduler.bookings (
    id UUID PRIMARY KEY,
    slot_id UUID NOT NULL REFERENCES scheduler.slot_instances(id) ON DELETE CASCADE,
    user_id UUID NULL REFERENCES public.auth_user(id),
    customer_name VARCHAR(255) NOT NULL,
    customer_phone VARCHAR(50) NOT NULL,
    customer_email VARCHAR(255) NULL,
    notes TEXT NULL,
    status VARCHAR(32) NOT NULL,
    hold_expires_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confirmed_at TIMESTAMPTZ NULL,
    cancelled_at TIMESTAMPTZ NULL,
    expired_at TIMESTAMPTZ NULL,
    consent JSONB NULL,
    source VARCHAR(64) NULL
);

-- Idempotency keys link booking requests with their resulting booking record.
CREATE TABLE IF NOT EXISTS scheduler.idempotency_keys (
    idempotency_key VARCHAR(255) PRIMARY KEY,
    booking_id UUID NOT NULL REFERENCES scheduler.bookings(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ensure updated_at columns reflect the last modification timestamp.
CREATE OR REPLACE FUNCTION scheduler.touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER locations_set_updated_at
BEFORE UPDATE ON scheduler.locations
FOR EACH ROW EXECUTE FUNCTION scheduler.touch_updated_at();

CREATE TRIGGER people_set_updated_at
BEFORE UPDATE ON scheduler.people
FOR EACH ROW EXECUTE FUNCTION scheduler.touch_updated_at();

CREATE TRIGGER availability_rules_set_updated_at
BEFORE UPDATE ON scheduler.availability_rules
FOR EACH ROW EXECUTE FUNCTION scheduler.touch_updated_at();

CREATE TRIGGER slot_instances_set_updated_at
BEFORE UPDATE ON scheduler.slot_instances
FOR EACH ROW EXECUTE FUNCTION scheduler.touch_updated_at();
