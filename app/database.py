"""
Async database access layer (SQLite via SQLAlchemy 2.0 + aiosqlite).

Everything the rest of the app needs from persistence goes through the
`Database` class: table creation, duplicate detection, saving a processed
message + its extraction, search/filtering, dashboard stats, daily
summaries, and flattened rows for CSV/Excel export.

The schema is created automatically on `init()` - no manual migrations are
needed for the SQLite-first setup described in the spec. Swapping to
PostgreSQL later only requires changing DATABASE_URL to an async postgres
DSN (e.g. postgresql+asyncpg://...); the SQLAlchemy models are portable.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Category, ExtractedInfo, ExtractionResult, TelegramMessage

logger = logging.getLogger(__name__)


@dataclass
class RawMessage:
    """Everything captured from an incoming Telegram message, pre-extraction."""

    message_id: int
    group_id: str
    group_name: str
    sender_id: Optional[str]
    sender_name: Optional[str]
    raw_text: str
    content_hash: str
    message_date: dt.datetime


def _enable_sqlite_wal(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """Enable WAL mode so the Telegram listener and the API server can both
    read/write the same SQLite file concurrently without locking errors."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Database:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine: AsyncEngine = create_async_engine(database_url, echo=False, future=True)
        if database_url.startswith("sqlite"):
            event.listen(self.engine.sync_engine, "connect", _enable_sqlite_wal)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        """Create all tables if they don't already exist."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database initialized at %s", self.database_url)

    async def close(self) -> None:
        await self.engine.dispose()

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    async def message_exists(self, group_id: str, message_id: int) -> bool:
        async with self.session_factory() as session:
            stmt = select(TelegramMessage.id).where(
                TelegramMessage.group_id == group_id,
                TelegramMessage.message_id == message_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def content_hash_exists(self, content_hash: str) -> bool:
        async with self.session_factory() as session:
            stmt = select(TelegramMessage.id).where(TelegramMessage.content_hash == content_hash)
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def save_extraction(
        self,
        raw: RawMessage,
        extraction: Optional[ExtractionResult],
        extraction_failed: bool = False,
    ) -> int:
        """Persist a raw message together with its (possibly failed) extraction.

        Returns the primary key of the stored TelegramMessage row.
        """
        async with self.session_factory() as session:
            async with session.begin():
                message = TelegramMessage(
                    message_id=raw.message_id,
                    group_id=raw.group_id,
                    group_name=raw.group_name,
                    sender_id=raw.sender_id,
                    sender_name=raw.sender_name,
                    raw_text=raw.raw_text,
                    content_hash=raw.content_hash,
                    message_date=raw.message_date,
                    processed=not extraction_failed,
                )
                session.add(message)
                await session.flush()  # populate message.id

                category_value = None
                if extraction and extraction.category:
                    category_value = (
                        extraction.category.value
                        if isinstance(extraction.category, Category)
                        else str(extraction.category)
                    )

                info = ExtractedInfo(
                    message_id=message.id,
                    category=category_value,
                    title=extraction.title if extraction else None,
                    summary=extraction.summary if extraction else None,
                    company=extraction.company if extraction else None,
                    role=extraction.role if extraction else None,
                    location=extraction.location if extraction else None,
                    deadline=extraction.deadline if extraction else None,
                    apply_link=extraction.apply_link if extraction else None,
                    contact=extraction.contact if extraction else None,
                    important_links=json.dumps(extraction.important_links if extraction else []),
                    skills=json.dumps(extraction.skills if extraction else []),
                    tags=json.dumps(extraction.tags if extraction else []),
                    is_useful=bool(extraction.is_useful) if extraction else False,
                    extraction_failed=extraction_failed,
                )
                session.add(info)
            return message.id

    # ------------------------------------------------------------------
    # Reads / search
    # ------------------------------------------------------------------

    def _row_to_dict(self, message: TelegramMessage, info: ExtractedInfo) -> dict[str, Any]:
        return {
            "id": info.id,
            "message_id": message.id,
            "telegram_message_id": message.message_id,
            "group_name": message.group_name,
            "sender_name": message.sender_name,
            "raw_text": message.raw_text,
            "message_date": message.message_date,
            "category": info.category,
            "title": info.title,
            "summary": info.summary,
            "company": info.company,
            "role": info.role,
            "location": info.location,
            "deadline": info.deadline,
            "apply_link": info.apply_link,
            "contact": info.contact,
            "important_links": json.loads(info.important_links or "[]"),
            "skills": json.loads(info.skills or "[]"),
            "tags": json.loads(info.tags or "[]"),
            "is_useful": info.is_useful,
            "created_at": info.created_at,
        }

    async def search(
        self,
        *,
        query: Optional[str] = None,
        category: Optional[str] = None,
        group_name: Optional[str] = None,
        is_useful: Optional[bool] = None,
        start_date: Optional[dt.datetime] = None,
        end_date: Optional[dt.datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            stmt = select(TelegramMessage, ExtractedInfo).join(
                ExtractedInfo, ExtractedInfo.message_id == TelegramMessage.id
            )
            if category:
                stmt = stmt.where(ExtractedInfo.category == category)
            if group_name:
                stmt = stmt.where(TelegramMessage.group_name == group_name)
            if is_useful is not None:
                stmt = stmt.where(ExtractedInfo.is_useful == is_useful)
            if start_date:
                stmt = stmt.where(TelegramMessage.message_date >= start_date)
            if end_date:
                stmt = stmt.where(TelegramMessage.message_date <= end_date)
            if query:
                like = f"%{query}%"
                stmt = stmt.where(
                    (TelegramMessage.raw_text.ilike(like))
                    | (ExtractedInfo.title.ilike(like))
                    | (ExtractedInfo.summary.ilike(like))
                )
            stmt = stmt.order_by(TelegramMessage.message_date.desc()).limit(limit).offset(offset)

            result = await session.execute(stmt)
            return [self._row_to_dict(message, info) for message, info in result.all()]

    async def get_by_id(self, extraction_id: int) -> Optional[dict[str, Any]]:
        async with self.session_factory() as session:
            stmt = (
                select(TelegramMessage, ExtractedInfo)
                .join(ExtractedInfo, ExtractedInfo.message_id == TelegramMessage.id)
                .where(ExtractedInfo.id == extraction_id)
            )
            result = await session.execute(stmt)
            row = result.first()
            return self._row_to_dict(*row) if row else None

    async def get_stats(self) -> dict[str, Any]:
        async with self.session_factory() as session:
            total_messages = (
                await session.execute(select(func.count(TelegramMessage.id)))
            ).scalar_one()
            total_useful = (
                await session.execute(
                    select(func.count(ExtractedInfo.id)).where(ExtractedInfo.is_useful.is_(True))
                )
            ).scalar_one()

            category_rows = await session.execute(
                select(ExtractedInfo.category, func.count(ExtractedInfo.id))
                .where(ExtractedInfo.is_useful.is_(True))
                .group_by(ExtractedInfo.category)
            )
            by_category = {cat or "Uncategorized": count for cat, count in category_rows.all()}

            group_rows = await session.execute(
                select(TelegramMessage.group_name, func.count(TelegramMessage.id)).group_by(
                    TelegramMessage.group_name
                )
            )
            by_group = {group: count for group, count in group_rows.all()}

            return {
                "total_messages": total_messages,
                "total_useful_extractions": total_useful,
                "by_category": by_category,
                "by_group": by_group,
            }

    async def get_daily_summary(self, day: dt.date) -> dict[str, Any]:
        start = dt.datetime.combine(day, dt.time.min)
        end = dt.datetime.combine(day, dt.time.max)
        records = await self.search(
            start_date=start, end_date=end, is_useful=True, limit=1000, offset=0
        )
        by_category: dict[str, int] = {}
        for record in records:
            cat = record["category"] or "Uncategorized"
            by_category[cat] = by_category.get(cat, 0) + 1
        return {
            "date": day.isoformat(),
            "total_useful": len(records),
            "by_category": by_category,
            "items": records,
        }

    async def get_all_for_export(
        self,
        *,
        category: Optional[str] = None,
        group_name: Optional[str] = None,
        is_useful: Optional[bool] = True,
    ) -> list[dict[str, Any]]:
        return await self.search(
            category=category,
            group_name=group_name,
            is_useful=is_useful,
            limit=100_000,
            offset=0,
        )
