from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.database import Database, RawMessage
from app.models import Category, ExtractionResult, TradeAction


def make_settings() -> Settings:
    return Settings(
        api_id=123,
        api_hash="hash",
        phone_number="+10000000000",
        session_name="test_session",
        telegram_groups=["test_group"],
        gemini_api_key="dummy",
        gemini_model="gemini-2.5-flash",
        gemini_max_retries=1,
        gemini_timeout_seconds=10.0,
        gemini_max_concurrent_requests=1,
        gemini_temperature=0.2,
        keywords=[],
        min_message_length=8,
        database_url="sqlite+aiosqlite:///:memory:",
        run_api=True,
        api_host="127.0.0.1",
        api_port=8000,
        daily_summary_enabled=False,
        daily_summary_hour=23,
        log_level="INFO",
    )


@pytest.fixture
def client(db: Database):
    async def _seed():
        raw = RawMessage(
            message_id=1,
            group_id="-100999",
            group_name="AI Jobs",
            sender_id="1",
            sender_name="alice",
            raw_text="We are hiring a Backend Engineer",
            content_hash="hash123",
            message_date=dt.datetime(2026, 7, 25, 9, 0, 0),
        )
        extraction = ExtractionResult(
            category=Category.JOB,
            title="Backend Engineer",
            summary="Hiring a backend engineer.",
            is_useful=True,
            skills=["Python"],
        )
        await db.save_extraction(raw, extraction)

    import asyncio

    asyncio.get_event_loop().run_until_complete(_seed())

    app = create_app(settings=make_settings(), db=db)
    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_messages(client: TestClient):
    response = client.get("/messages")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Backend Engineer"


def test_get_message_by_id(client: TestClient):
    listing = client.get("/messages").json()
    extraction_id = listing[0]["id"]
    response = client.get(f"/messages/{extraction_id}")
    assert response.status_code == 200
    assert response.json()["company"] is None


def test_get_message_not_found(client: TestClient):
    response = client.get("/messages/99999")
    assert response.status_code == 404


def test_stats(client: TestClient):
    response = client.get("/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_messages"] == 1
    assert data["by_category"]["Job"] == 1


def test_export_csv(client: TestClient):
    response = client.get("/export/csv")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert b"Backend Engineer" in response.content


@pytest.fixture
def stock_client(db: Database):
    async def _seed():
        raw = RawMessage(
            message_id=2,
            group_id="-100888",
            group_name="Share Market Tips",
            sender_id="2",
            sender_name="tipster",
            raw_text="Buy RELIANCE above 2450, target 2600, SL 2400",
            content_hash="stockhash1",
            message_date=dt.datetime(2026, 7, 25, 9, 0, 0),
        )
        extraction = ExtractionResult(
            category=Category.STOCK_TIP,
            title="Buy RELIANCE",
            is_useful=True,
            stock_symbol="RELIANCE",
            stock_name="Reliance Industries",
            exchange="NSE",
            action=TradeAction.BUY,
            target_price="2600",
            stop_loss="2400",
        )
        await db.save_extraction(raw, extraction)

    import asyncio

    asyncio.get_event_loop().run_until_complete(_seed())

    app = create_app(settings=make_settings(), db=db)
    with TestClient(app) as test_client:
        yield test_client


def test_stock_insights_endpoint(stock_client: TestClient):
    response = stock_client.get("/insights/stocks")
    assert response.status_code == 200
    data = response.json()
    assert data["total_tips"] == 1
    assert data["buy_count"] == 1
    assert data["top_stocks"][0]["symbol"] == "RELIANCE"


def test_stock_history_endpoint(stock_client: TestClient):
    response = stock_client.get("/insights/stocks/reliance")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["target_price"] == "2600"


def test_stock_history_endpoint_not_found(stock_client: TestClient):
    response = stock_client.get("/insights/stocks/UNKNOWN")
    assert response.status_code == 404
