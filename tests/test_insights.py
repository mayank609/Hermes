from __future__ import annotations

import datetime as dt

from app.database import Database, RawMessage
from app.insights import format_stock_digest
from app.models import Category, ExtractionResult, TradeAction


async def _add_tip(
    db: Database,
    *,
    message_id: int,
    text: str,
    symbol: str,
    action: TradeAction,
    target: str | None = None,
    stop_loss: str | None = None,
    message_date: dt.datetime,
) -> None:
    raw = RawMessage(
        message_id=message_id,
        group_id="-100777",
        group_name="Share Market Tips",
        sender_id="1",
        sender_name="tipster",
        raw_text=text,
        content_hash=f"hash-{message_id}",
        message_date=message_date,
    )
    extraction = ExtractionResult(
        category=Category.STOCK_TIP,
        title=f"{action.value} {symbol}",
        is_useful=True,
        stock_symbol=symbol,
        stock_name=symbol,
        exchange="NSE",
        action=action,
        entry_price="100",
        target_price=target,
        stop_loss=stop_loss,
        timeframe="Short-term",
        source="Test Analyst",
    )
    await db.save_extraction(raw, extraction)


async def test_get_stock_insights_aggregates_by_symbol(db: Database):
    base = dt.datetime(2026, 7, 25, 9, 0, 0)
    await _add_tip(
        db, message_id=1, text="Buy RELIANCE above 2450, target 2600, SL 2400",
        symbol="RELIANCE", action=TradeAction.BUY, target="2600", stop_loss="2400",
        message_date=base,
    )
    await _add_tip(
        db, message_id=2, text="Sell RELIANCE below 2500, target 2350",
        symbol="RELIANCE", action=TradeAction.SELL, target="2350",
        message_date=base + dt.timedelta(hours=1),
    )
    await _add_tip(
        db, message_id=3, text="Buy TCS at CMP, target 3900",
        symbol="TCS", action=TradeAction.BUY, target="3900",
        message_date=base + dt.timedelta(hours=2),
    )

    summary = await db.get_stock_insights(limit=10)

    assert summary["total_tips"] == 3
    assert summary["buy_count"] == 2
    assert summary["sell_count"] == 1
    assert summary["hold_count"] == 0

    top = {s["symbol"]: s for s in summary["top_stocks"]}
    assert top["RELIANCE"]["mentions"] == 2
    assert top["RELIANCE"]["buy"] == 1
    assert top["RELIANCE"]["sell"] == 1
    # Most recent RELIANCE row (message 2, later timestamp) should be "latest"
    assert top["RELIANCE"]["latest_action"] == "Sell"
    assert top["RELIANCE"]["latest_target"] == "2350"
    assert top["TCS"]["mentions"] == 1


async def test_get_stock_insights_respects_date_range(db: Database):
    await _add_tip(
        db, message_id=1, text="Buy INFY, target 1600",
        symbol="INFY", action=TradeAction.BUY, target="1600",
        message_date=dt.datetime(2026, 7, 20, 10, 0, 0),
    )
    await _add_tip(
        db, message_id=2, text="Buy WIPRO, target 500",
        symbol="WIPRO", action=TradeAction.BUY, target="500",
        message_date=dt.datetime(2026, 7, 25, 10, 0, 0),
    )

    summary = await db.get_stock_insights(
        start_date=dt.datetime(2026, 7, 25, 0, 0, 0),
        end_date=dt.datetime(2026, 7, 25, 23, 59, 59),
    )

    assert summary["total_tips"] == 1
    assert summary["top_stocks"][0]["symbol"] == "WIPRO"


async def test_get_stock_history_matches_symbol_case_insensitively(db: Database):
    base = dt.datetime(2026, 7, 25, 9, 0, 0)
    await _add_tip(
        db, message_id=1, text="Buy HDFC, target 1700",
        symbol="HDFC", action=TradeAction.BUY, target="1700",
        message_date=base,
    )

    history = await db.get_stock_history("hdfc")

    assert len(history) == 1
    assert history[0]["stock_symbol"] == "HDFC"
    assert history[0]["target_price"] == "1700"


async def test_get_stock_history_returns_empty_for_unknown_symbol(db: Database):
    history = await db.get_stock_history("NOPE")
    assert history == []


def test_format_stock_digest_with_no_tips():
    text = format_stock_digest("2026-07-25", {"total_tips": 0, "buy_count": 0, "sell_count": 0, "hold_count": 0, "top_stocks": []})
    assert "No stock tips" in text


def test_format_stock_digest_with_tips():
    summary = {
        "total_tips": 2,
        "buy_count": 1,
        "sell_count": 1,
        "hold_count": 0,
        "top_stocks": [
            {
                "symbol": "RELIANCE",
                "name": "RELIANCE",
                "exchange": "NSE",
                "mentions": 2,
                "buy": 1,
                "sell": 1,
                "hold": 0,
                "latest_action": "Sell",
                "latest_target": "2350",
                "latest_stop_loss": None,
            }
        ],
    }
    text = format_stock_digest("2026-07-25", summary)
    assert "RELIANCE" in text
    assert "Total tips: 2" in text
    assert "latest call: Sell" in text
