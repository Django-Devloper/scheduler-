-- Init script to create placeholder public schema tables for local development
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS public.biz_entity (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL
);

INSERT INTO public.biz_entity (id, name)
VALUES (uuid_generate_v4(), 'Demo Business')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS public.auth_user (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username VARCHAR(150) NOT NULL UNIQUE,
    email VARCHAR(254),
    password VARCHAR(128) NOT NULL DEFAULT ''
);

INSERT INTO public.auth_user (id, username, email, password)
VALUES (uuid_generate_v4(), 'demo-user', 'demo@example.com', '')
ON CONFLICT (username) DO NOTHING;
