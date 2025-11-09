-- Optional helper for local development only.
-- Provides minimal versions of the shared tables that the scheduler schema references.

CREATE TABLE IF NOT EXISTS public.auth_user (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.biz_entity (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Ensure the dedicated scheduler schema exists for the FastAPI service.
CREATE SCHEMA IF NOT EXISTS scheduler;
