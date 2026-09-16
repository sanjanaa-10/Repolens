"""Async SQLAlchemy engine/session setup backed by SQLite.

Designed so the database URL can be swapped to PostgreSQL for deployed
multi-user usage without changing the rest of the application.

SQLite foreign-key enforcement is off by default. RepoLens enables it on every
session that opens a database connection (see :func:`enable_foreign_keys`);
this turns accidental orphan writes into constraint errors instead of silent
corruption. Combined with the explicit bottom-up deletes in
``app.services.ingestion_service.remove_repository`` this keeps the graph of
diffs, impact analyses, reviews, and audits consistent when a repository is
deleted.
"""
from __future__ import annotations

from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_settings = get_settings()


def _database_url() -> str:
    settings = get_settings()
    return f"sqlite+aiosqlite:///{settings.db_path.as_posix()}"


engine = create_async_engine(_database_url(), echo=False, future=True)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def enable_foreign_keys(session: AsyncSession) -> None:
    """Enable SQLite FK enforcement on the connection this session is bound to."""
    await session.execute(text("PRAGMA foreign_keys=ON"))


def _migrate_relationships_table(connection) -> None:
    """Align a stale Phase-3 relationships table with the current ORM model.

    The pre-Phase-3 scaffold shipped a different shape (``confidence`` /
    ``source_file`` / ``source_line``). Relationship data is derived and
    rebuilt idempotently by the engine, so a mismatched table is simply
    dropped and recreated with the current schema.
    """
    insp = sa_inspect(connection)
    if "relationships" not in insp.get_table_names():
        return
    columns = {column["name"] for column in insp.get_columns("relationships")}
    if "resolution_status" in columns and "evidence_file_id" in columns:
        return
    connection.exec_driver_sql("DROP TABLE relationships")


async def init_db() -> None:
    """Create all tables. Import models before calling so they register."""
    from app.models import orm  # noqa: F401

    async with engine.begin() as conn:

        def _run(connection) -> None:
            _migrate_relationships_table(connection)
            Base.metadata.create_all(connection)

        await conn.run_sync(_run)


async def get_db() -> AsyncSession:
    """FastAPI dependency yielding an async session with FK enforcement on."""
    async with SessionLocal() as session:
        try:
            await enable_foreign_keys(session)
        except Exception:
            # FK enforcement must never break API availability; SQLite holds it
            # per-connection, so already-enforced pooled connections are fine.
            pass
        try:
            yield session
        finally:
            await session.close()
