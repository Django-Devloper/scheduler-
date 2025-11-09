from __future__ import annotations

import os
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .models import metadata, SCHEDULER_SCHEMA


def _default_database_url() -> str:
    user = os.getenv("POSTGRES_USER", "scheduler")
    password = os.getenv("POSTGRES_PASSWORD", "scheduler")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db_name = os.getenv("POSTGRES_DB", "scheduler")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db_name}"


DATABASE_URL = os.getenv("DATABASE_URL", _default_database_url())


engine: AsyncEngine = create_async_engine(DATABASE_URL, echo=False, future=True)
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Import your SQLAlchemy MetaData and the target schema name
from .models import metadata, SCHEDULER_SCHEMA


# ---------- URL builders: return URL objects (not strings) ----------

def _default_database_url() -> URL:
    """
    Build a safe async URL. Returning a URL object avoids string round-trip
    issues with special characters (e.g., '@', ':', '/').
    """
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "Dipak@123")
    host = os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("DB_PORT", "5432"))
    db_name = os.getenv("DB_NAME", "postgres")

    return URL.create(
        drivername="postgresql+asyncpg",
        username=user,
        password=password,   # handled safely by SQLAlchemy
        host=host,
        port=port,
        database=db_name,
    )


def _resolve_database_url() -> URL:
    """
    Respect DATABASE_URL if provided. If it's a sync URL (e.g., 'postgresql://'),
    upgrade it to 'postgresql+asyncpg', and ALWAYS return a URL object.
    """
    env_url = os.getenv("DATABASE_URL")
    if not env_url:
        return _default_database_url()

    try:
        u = make_url(env_url)
        # Force async driver
        if not u.drivername.startswith("postgresql+asyncpg"):
            u = u.set(drivername="postgresql+asyncpg")
        return u
    except Exception:
        # If parsing fails for some reason, fall back to defaults
        return _default_database_url()


# Use a URL object end-to-end to prevent password mangling
DATABASE_URL: URL = _resolve_database_url()

engine: AsyncEngine = create_async_engine(
    DATABASE_URL,          # <-- URL object (not str)
    echo=False,
    future=True,
    pool_pre_ping=True,    # helps avoid stale connections
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


@asynccontextmanager
async def get_session() -> AsyncSession:
    async with async_session_factory() as session:  # type: ignore[misc]
async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


async def initialize_schema() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEDULER_SCHEMA}"'))
        await conn.run_sync(metadata.create_all)
    """
    - Ensures the target schema exists
    - Sets search_path so create_all puts tables into that schema if your models
      don't already specify 'schema=SCHEDULER_SCHEMA'
    - Creates all tables defined in `metadata`
    """
    async with engine.begin() as conn:
        # 1) Create schema if missing
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEDULER_SCHEMA}"'))

        # 2) Ensure objects are created in the intended schema when models lack explicit schema=
        await conn.execute(text(f'SET search_path TO "{SCHEDULER_SCHEMA}"'))

        # 3) Create tables
        await conn.run_sync(metadata.create_all, checkfirst=True)


# Optional: a quick connectivity check you can call during startup
async def ping() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
