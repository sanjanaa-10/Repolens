"""Persistence and read service for Phase 7 change reviews.

All queries are repository-scoped. Creating a review is idempotent per
(repository, diff, impact analysis): the latest existing review is reused
unless ``regenerate`` is requested, in which case a fresh review is created and
reviewer state (item status/notes, entry status) is carried over by stable
identity keys.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.impact.service import (
    ImpactNotFoundError,
    analysis_to_info,
    get_impact_analysis,
)
from app.analysis.review.generator import (
    ReviewSpec,
    clean_text,
    generate_review_spec,
)
from app.analysis.review.policy import MAX_REVIEW_NOTES_LENGTH
from app.core.locks import protected
from app.models.orm import Review, ReviewItem, ReviewItemEntry
from app.models.schemas import (
    ReviewEntryInfo,
    ReviewEntryStatus,
    ReviewInfo,
    ReviewItemInfo,
    ReviewItemStatus,
    ReviewSummaryInfo,
)


class ReviewNotFoundError(LookupError):
    pass


class ReviewItemNotFoundError(LookupError):
    pass


async def _latest_review(
    db: AsyncSession, repository_id: int, diff_id: int, analysis_id: int
) -> Review | None:
    result = await db.execute(
        select(Review)
        .where(
            Review.repository_id == repository_id,
            Review.diff_id == diff_id,
            Review.impact_analysis_id == analysis_id,
        )
        .order_by(Review.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _load_items(
    db: AsyncSession, review: Review
) -> list[tuple[ReviewItem, list[ReviewItemEntry]]]:
    items = list(
        (
            await db.execute(
                select(ReviewItem)
                .where(ReviewItem.review_id == review.id)
                .order_by(ReviewItem.sort, ReviewItem.stable_key)
            )
        )
        .scalars()
        .all()
    )
    result: list[tuple[ReviewItem, list[ReviewItemEntry]]] = []
    for item in items:
        entries = list(
            (
                await db.execute(
                    select(ReviewItemEntry)
                    .where(ReviewItemEntry.review_item_id == item.id)
                    .order_by(ReviewItemEntry.sort, ReviewItemEntry.entry_key)
                )
            )
            .scalars()
            .all()
        )
        result.append((item, entries))
    return result


async def _carryover_state(
    db: AsyncSession, review: Review
) -> tuple[dict[str, tuple[str, str, str, str]], dict[str, str]]:
    """Map prior item status+notes by stable key and entry status by entry key."""
    item_map: dict[str, tuple[str, str]] = {}
    entry_map: dict[str, str] = {}
    rows = await _load_items(db, review)
    for item, entries in rows:
        item_map[item.stable_key] = (item.status, item.notes)
        for entry in entries:
            entry_map[entry.entry_key] = entry.status
    return item_map, entry_map


async def _persist_spec(
    db: AsyncSession,
    review: Review,
    spec: ReviewSpec,
    item_state: dict[str, tuple[str, str]],
    entry_state: dict[str, str],
) -> None:
    completed = 0
    required = 0
    recommended = 0
    informational = 0
    for index, item_spec in enumerate(spec.items):
        prior_status, prior_notes = item_state.get(item_spec.stable_key, ("OPEN", ""))
        status = prior_status if prior_status in {"OPEN", "IN_PROGRESS", "DONE", "SKIPPED"} else "OPEN"
        if status == "DONE":
            completed += 1
        if item_spec.priority == "REQUIRED":
            required += 1
        elif item_spec.priority == "RECOMMENDED":
            recommended += 1
        else:
            informational += 1

        item = ReviewItem(
            review_id=review.id,
            stable_key=item_spec.stable_key,
            item_type=item_spec.item_type,
            title=item_spec.title,
            description=item_spec.description,
            status=status,
            priority=item_spec.priority,
            source_type=item_spec.source_type,
            source_id=item_spec.source_id,
            evidence_file=item_spec.evidence_file,
            evidence_start_line=item_spec.evidence_start_line,
            evidence_end_line=item_spec.evidence_end_line,
            group_via=item_spec.group_via,
            sort=item_spec.sort,
            notes=prior_notes[:MAX_REVIEW_NOTES_LENGTH],
        )
        db.add(item)
        await db.flush()
        for entry_spec in item_spec.entries:
            db.add(
                ReviewItemEntry(
                    review_item_id=item.id,
                    entry_key=entry_spec.entry_key,
                    title=entry_spec.title,
                    description=entry_spec.description,
                    kind=entry_spec.kind,
                    status=entry_state.get(entry_spec.entry_key, "OPEN"),
                    symbol_id=entry_spec.symbol_id,
                    file_path=entry_spec.file_path,
                    evidence_file=entry_spec.evidence_file,
                    evidence_line=entry_spec.evidence_line,
                    path_steps=json.dumps(entry_spec.path_steps),
                    sort=entry_spec.sort,
                )
            )

    review.total_items = len(spec.items)
    review.required_count = required
    review.recommended_count = recommended
    review.informational_count = informational
    review.completed_count = completed


async def create_review(
    db: AsyncSession, repository_id: int, analysis_id: int, regenerate: bool = False
) -> tuple[Review, bool]:
    """Create (or reuse) a review for a repository's impact analysis.

    Returns ``(review, created)`` where ``created`` distinguishes a fresh review
    from a reused one (used by the route to set the HTTP status code).
    """
    async with protected(f"review:{repository_id}:{analysis_id}:{regenerate}"):
        return await _create_review_unlocked(
            db, repository_id, analysis_id, regenerate
        )


async def _create_review_unlocked(
    db: AsyncSession, repository_id: int, analysis_id: int, regenerate: bool = False
) -> tuple[Review, bool]:
    """Create (or reuse) a review for a repository's impact analysis.

    Returns ``(review, created)`` where ``created`` distinguishes a fresh review
    from a reused one (used by the route to set the HTTP status code).
    """
    try:
        analysis = await get_impact_analysis(db, repository_id, analysis_id)
    except ImpactNotFoundError as exc:
        raise ReviewNotFoundError(f"impact analysis {analysis_id} not found") from exc

    info = await analysis_to_info(db, analysis)
    spec = generate_review_spec(info)

    previous = await _latest_review(
        db, repository_id, analysis.diff_id, analysis.id
    )
    if previous is not None and not regenerate:
        return previous, False

    item_state: dict[str, tuple[str, str]] = {}
    entry_state: dict[str, str] = {}
    if previous is not None and regenerate:
        item_state, entry_state = await _carryover_state(db, previous)

    review = Review(
        repository_id=repository_id,
        diff_id=analysis.diff_id,
        impact_analysis_id=analysis.id,
        title=spec.title,
        summary=spec.summary,
        status="OPEN",
        base_revision=analysis.base_revision,
        head_revision=analysis.head_revision,
        max_depth=analysis.max_depth,
        total_items=len(spec.items),
        required_count=spec.count("REQUIRED"),
        recommended_count=spec.count("RECOMMENDED"),
        informational_count=spec.count("INFORMATIONAL"),
        completed_count=0,
    )
    db.add(review)
    await db.flush()
    await _persist_spec(db, review, spec, item_state, entry_state)
    await db.commit()
    await db.refresh(review)
    return review, True


async def get_review(
    db: AsyncSession, repository_id: int, review_id: int
) -> Review:
    result = await db.execute(
        select(Review).where(
            Review.id == review_id,
            Review.repository_id == repository_id,
        )
    )
    review = result.scalar_one_or_none()
    if review is None:
        raise ReviewNotFoundError(f"review {review_id} not found")
    return review


async def get_item(
    db: AsyncSession, repository_id: int, review_id: int, item_id: int
) -> ReviewItem:
    review = await get_review(db, repository_id, review_id)
    result = await db.execute(
        select(ReviewItem).where(
            ReviewItem.id == item_id,
            ReviewItem.review_id == review.id,
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise ReviewItemNotFoundError(f"review item {item_id} not found")
    return item


async def update_item(
    db: AsyncSession,
    repository_id: int,
    review_id: int,
    item_id: int,
    status: ReviewItemStatus | None = None,
    notes: str | None = None,
) -> Review:
    review = await get_review(db, repository_id, review_id)
    item = await get_item(db, repository_id, review_id, item_id)
    if status is not None:
        item.status = status.value
    if notes is not None:
        item.notes = clean_text(notes)[:MAX_REVIEW_NOTES_LENGTH]
    review.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(review)
    return review


async def get_entry(
    db: AsyncSession, repository_id: int, review_id: int, item_id: int, entry_id: int
) -> ReviewItemEntry:
    item = await get_item(db, repository_id, review_id, item_id)
    result = await db.execute(
        select(ReviewItemEntry).where(
            ReviewItemEntry.id == entry_id,
            ReviewItemEntry.review_item_id == item.id,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise ReviewItemNotFoundError(f"review entry {entry_id} not found")
    return entry


async def update_entry(
    db: AsyncSession,
    repository_id: int,
    review_id: int,
    item_id: int,
    entry_id: int,
    status: ReviewEntryStatus,
) -> Review:
    review = await get_review(db, repository_id, review_id)
    entry = await get_entry(db, repository_id, review_id, item_id, entry_id)
    entry.status = status.value
    review.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(review)
    return review


async def review_to_info(db: AsyncSession, review: Review) -> ReviewInfo:
    item_infos = []
    completed = 0
    open_count = 0
    in_progress = 0
    skipped = 0
    for item, entries in await _load_items(db, review):
        if item.status == "DONE":
            completed += 1
        elif item.status == "OPEN":
            open_count += 1
        elif item.status == "IN_PROGRESS":
            in_progress += 1
        else:
            skipped += 1
        item_infos.append(
            ReviewItemInfo(
                item_id=item.id,
                item_type=item.item_type,
                title=item.title,
                description=item.description,
                status=ReviewItemStatus(item.status),
                priority=item.priority,
                source_type=item.source_type,
                source_id=item.source_id,
                evidence_file=item.evidence_file,
                evidence_start_line=item.evidence_start_line,
                evidence_end_line=item.evidence_end_line,
                group_via=item.group_via,
                notes=item.notes,
                entries=[entry_to_info(entry) for entry in entries],
            )
        )

    return ReviewInfo(
        review_id=review.id,
        repository_id=review.repository_id,
        diff_id=review.diff_id,
        impact_analysis_id=review.impact_analysis_id,
        title=review.title,
        summary=review.summary,
        status=ReviewItemStatus(review.status),
        base_revision=review.base_revision,
        head_revision=review.head_revision,
        max_depth=review.max_depth,
        created_at=review.created_at,
        updated_at=review.updated_at,
        review_summary=ReviewSummaryInfo(
            total_items=len(item_infos),
            required=review.required_count,
            recommended=review.recommended_count,
            informational=review.informational_count,
            completed=completed,
            open=open_count,
            in_progress=in_progress,
            skipped=skipped,
        ),
        items=item_infos,
    )


async def item_to_info(db: AsyncSession, item: ReviewItem) -> ReviewItemInfo:
    entries = list(
        (
            await db.execute(
                select(ReviewItemEntry)
                .where(ReviewItemEntry.review_item_id == item.id)
                .order_by(ReviewItemEntry.sort, ReviewItemEntry.entry_key)
            )
        )
        .scalars()
        .all()
    )
    return ReviewItemInfo(
        item_id=item.id,
        item_type=item.item_type,
        title=item.title,
        description=item.description,
        status=ReviewItemStatus(item.status),
        priority=item.priority,
        source_type=item.source_type,
        source_id=item.source_id,
        evidence_file=item.evidence_file,
        evidence_start_line=item.evidence_start_line,
        evidence_end_line=item.evidence_end_line,
        group_via=item.group_via,
        notes=item.notes,
        entries=[entry_to_info(entry) for entry in entries],
    )


def entry_to_info(entry: ReviewItemEntry) -> ReviewEntryInfo:
    return ReviewEntryInfo(
        entry_id=entry.id,
        key=entry.entry_key,
        title=entry.title,
        description=entry.description,
        kind=entry.kind,
        status=ReviewEntryStatus(entry.status),
        symbol_id=entry.symbol_id,
        file_path=entry.file_path,
        evidence_file=entry.evidence_file,
        evidence_line=entry.evidence_line,
        path_steps=json.loads(entry.path_steps or "[]"),
    )
