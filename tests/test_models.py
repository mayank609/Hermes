from __future__ import annotations

from app.models import Category, ExtractionResult, TradeAction


def test_extraction_result_parses_stock_tip():
    data = {
        "category": "Stock Tip",
        "title": "Buy RELIANCE",
        "summary": "Analyst recommends buying Reliance with a target of 2600.",
        "stock_name": "Reliance Industries",
        "stock_symbol": "RELIANCE",
        "exchange": "NSE",
        "action": "Buy",
        "entry_price": "2450",
        "target_price": "2600",
        "stop_loss": "2400",
        "timeframe": "Short-term",
        "source": "XYZ Research",
        "is_useful": True,
    }
    result = ExtractionResult.model_validate(data)
    assert result.category == Category.STOCK_TIP.value
    assert result.action == TradeAction.BUY.value
    assert result.stock_symbol == "RELIANCE"
    assert result.target_price == "2600"


def test_extraction_result_finance_fields_default_none():
    result = ExtractionResult()
    assert result.stock_name is None
    assert result.action is None


def test_extraction_result_parses_full_schema():
    data = {
        "category": "Hackathon",
        "title": "Global AI Hackathon 2026",
        "summary": "48-hour hackathon focused on applied AI.",
        "company": None,
        "role": None,
        "location": "Bangalore",
        "deadline": "2026-09-01",
        "apply_link": "https://example.com/apply",
        "contact": "team@example.com",
        "important_links": ["https://example.com/apply", "https://example.com/rules"],
        "skills": ["Python", "ML"],
        "tags": ["hackathon", "ai"],
        "is_useful": True,
    }
    result = ExtractionResult.model_validate(data)
    assert result.category == Category.HACKATHON.value
    assert result.is_useful is True
    assert len(result.important_links) == 2


def test_extraction_result_parses_irrelevant_shorthand():
    result = ExtractionResult.model_validate({"is_useful": False})
    assert result.is_useful is False
    assert result.category is None
    assert result.important_links == []
    assert result.skills == []
    assert result.tags == []


def test_extraction_result_defaults_is_useful_false():
    result = ExtractionResult()
    assert result.is_useful is False
