"""Run the relationship engine over real repos and print metrics."""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault(
    "REPOLENS_DB_PATH", os.path.join(os.getcwd(), "data", "repolens.db")
)


async def run(repo_id: int, name: str):
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

    from app.analysis.relationships.engine import RelationshipEngine
    from app.models.orm import Relationship, Symbol

    engine = create_async_engine("sqlite+aiosqlite:///./data/repolens.db")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        t0 = time.perf_counter()
        result = await RelationshipEngine(session).build_relationships(repo_id)
        wall = time.perf_counter() - t0
        print(f"\n=== {name} (repo {repo_id}) ===")
        for k, v in result.items():
            print(f"  {k}: {v}")
        print(f"  wall_seconds: {wall:.2f}")

        rows = (
            await session.execute(
                select(Relationship.type, Relationship.resolution_status, func.count())
                .where(Relationship.repository_id == repo_id)
                .group_by(Relationship.type, Relationship.resolution_status)
            )
        ).all()
        print("  edges by type/status:")
        for rtype, status, cnt in rows:
            print(f"    {rtype:12s} {status:10s} {cnt}")

        sample = (
            await session.execute(
                select(Relationship.evidence)
                .join(Symbol, Relationship.target_symbol_id == Symbol.id)
                .where(
                    Relationship.repository_id == repo_id,
                    Relationship.type == "CALLS",
                    Relationship.resolution_status == "RESOLVED",
                )
                .limit(8)
            )
        ).scalars().all()
        print("  sample resolved CALLS:")
        for s in sample:
            print("   -", s)
    await engine.dispose()


async def main():
    from app.core.database import init_db

    await init_db()
    await run(1, "itsdangerous")
    await run(2, "flask")


asyncio.run(main())