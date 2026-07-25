"""
Optional FastAPI REST API for browsing, searching, and exporting extracted
information. Runs in the same process as the Telegram listener (see
main.py) and reads from the same SQLite database.

Endpoints:
  GET  /health                 liveness check
  GET  /messages               search/filter extracted items
  GET  /messages/{id}          fetch a single extraction by id
  GET  /stats                  dashboard-ready counts (by category/group)
  GET  /summary/daily          daily summary for a given date
  GET  /export/csv             export filtered results as CSV
  GET  /export/excel           export filtered results as .xlsx
"""

from __future__ import annotations

import datetime as dt
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

from app.config import Settings, get_settings
from app.database import Database
from app.exporter import export_to_csv_bytes, export_to_excel_bytes
from app.models import ExtractionResponse

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, db: Database | None = None) -> FastAPI:
    """App factory so tests can inject a Settings/Database instead of reading
    the real environment / hitting the real database file."""

    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = db or Database(settings.database_url)
        await app.state.db.init()
        logger.info("API server ready")
        yield
        await app.state.db.close()

    app = FastAPI(
        title="Telegram Information Extraction Agent API",
        description="Search, browse, and export information extracted from monitored Telegram groups.",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/messages", response_model=list[ExtractionResponse])
    async def list_messages(
        q: str | None = Query(None, description="Free-text search over title/summary/raw text"),
        category: str | None = Query(None),
        group_name: str | None = Query(None),
        is_useful: bool | None = Query(True),
        start_date: dt.datetime | None = Query(None),
        end_date: dt.datetime | None = Query(None),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        return await app.state.db.search(
            query=q,
            category=category,
            group_name=group_name,
            is_useful=is_useful,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )

    @app.get("/messages/{extraction_id}", response_model=ExtractionResponse)
    async def get_message(extraction_id: int):
        record = await app.state.db.get_by_id(extraction_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Extraction not found")
        return record

    @app.get("/stats")
    async def stats():
        return await app.state.db.get_stats()

    @app.get("/summary/daily")
    async def daily_summary(date: dt.date | None = Query(None, description="Defaults to today (UTC)")):
        day = date or dt.datetime.utcnow().date()
        return await app.state.db.get_daily_summary(day)

    @app.get("/export/csv")
    async def export_csv(
        category: str | None = Query(None),
        group_name: str | None = Query(None),
        is_useful: bool | None = Query(True),
    ):
        records = await app.state.db.get_all_for_export(
            category=category, group_name=group_name, is_useful=is_useful
        )
        content = export_to_csv_bytes(records)
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=extractions.csv"},
        )

    @app.get("/export/excel")
    async def export_excel(
        category: str | None = Query(None),
        group_name: str | None = Query(None),
        is_useful: bool | None = Query(True),
    ):
        records = await app.state.db.get_all_for_export(
            category=category, group_name=group_name, is_useful=is_useful
        )
        content = export_to_excel_bytes(records)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=extractions.xlsx"},
        )

    return app
