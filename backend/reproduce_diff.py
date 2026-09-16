"""Reproduce the psf/requests empty-tree diff failure with full traceback."""
import asyncio
import logging
import sys
import traceback

logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)

from app.analysis.diff.engine import DiffEngine  # noqa: E402
from app.core.database import SessionLocal, enable_foreign_keys  # noqa: E402

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


async def main() -> None:
    async with SessionLocal() as db:
        await enable_foreign_keys(db)
        engine = DiffEngine(db)
        for label, base, head in [
            ("EMPTY_TREE -> HEAD", EMPTY_TREE, "HEAD"),
            ("HEAD~1 -> HEAD", "HEAD~1", "HEAD"),
        ]:
            print(f"\n=== {label} ===")
            try:
                summary = await engine.analyze(3, base, head)
                print("OK:", summary)
            except Exception:  # noqa: BLE001
                traceback.print_exc()


asyncio.run(main())