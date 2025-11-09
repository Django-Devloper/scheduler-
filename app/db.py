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

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_session() -> AsyncSession:
    async with async_session_factory() as session:  # type: ignore[misc]
        yield session


async def initialize_schema() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEDULER_SCHEMA}"'))
        await conn.run_sync(metadata.create_all)
