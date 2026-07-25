from __future__ import annotations

import datetime as dt

import pytest

from app.extractor import MessageProcessor, NewMessage, compute_content_hash
from app.gemini import GeminiExtractionError
from app.models import Category, ExtractionResult


class FakeGemini:
    """Stands in for GeminiExtractor so tests never hit the network."""

    def __init__(self, result: ExtractionResult | None = None, raise_error: bool = False):
        self.result = result or ExtractionResult(
            category=Category.JOB,
            title="Backend Engineer at Acme",
            summary="Acme is hiring a backend engineer.",
            company="Acme",
            role="Backend Engineer",
            location="Remote",
            deadline="2026-08-01",
            apply_link="https://acme.example/careers/123",
            important_links=["https://acme.example/careers/123"],
            skills=["Python", "SQL"],
            tags=["job", "backend"],
            is_useful=True,
        )
        self.raise_error = raise_error
        self.calls: list[str] = []

    async def extract(self, text: str) -> ExtractionResult:
        self.calls.append(text)
        if self.raise_error:
            raise GeminiExtractionError("simulated failure")
        return self.result


def make_message(**overrides) -> NewMessage:
    defaults = dict(
        message_id=1,
        group_id="-100123",
        group_name="AI Jobs Group",
        sender_id="42",
        sender_name="alice",
        raw_text="We are hiring a Backend Engineer at Acme! Apply: https://acme.example/careers/123",
        message_date=dt.datetime(2026, 7, 25, 10, 0, 0),
    )
    defaults.update(overrides)
    return NewMessage(**defaults)


async def test_process_stores_useful_extraction(db):
    gemini = FakeGemini()
    processor = MessageProcessor(gemini, db, keywords=[], min_message_length=8)

    handled = await processor.process(make_message())

    assert handled is True
    assert len(gemini.calls) == 1
    results = await db.search(is_useful=True)
    assert len(results) == 1
    assert results[0]["title"] == "Backend Engineer at Acme"
    assert results[0]["category"] == "Job"
    assert results[0]["skills"] == ["Python", "SQL"]


async def test_process_skips_duplicate_message_id(db):
    gemini = FakeGemini()
    processor = MessageProcessor(gemini, db, keywords=[], min_message_length=8)

    await processor.process(make_message())
    handled_again = await processor.process(make_message())

    assert handled_again is False
    assert len(gemini.calls) == 1  # Gemini was not called a second time


async def test_process_skips_duplicate_content_across_messages(db):
    gemini = FakeGemini()
    processor = MessageProcessor(gemini, db, keywords=[], min_message_length=8)

    await processor.process(make_message(message_id=1))
    handled_again = await processor.process(make_message(message_id=2))

    assert handled_again is False
    assert len(gemini.calls) == 1


async def test_process_skips_short_messages(db):
    gemini = FakeGemini()
    processor = MessageProcessor(gemini, db, keywords=[], min_message_length=20)

    handled = await processor.process(make_message(raw_text="hi"))

    assert handled is False
    assert gemini.calls == []


async def test_keyword_filter_blocks_non_matching_messages(db):
    gemini = FakeGemini()
    processor = MessageProcessor(gemini, db, keywords=["hackathon"], min_message_length=8)

    handled = await processor.process(make_message(raw_text="Just chatting about the weather today"))

    assert handled is False
    assert gemini.calls == []


async def test_keyword_filter_allows_matching_messages(db):
    gemini = FakeGemini()
    processor = MessageProcessor(gemini, db, keywords=["hiring"], min_message_length=8)

    handled = await processor.process(make_message())

    assert handled is True
    assert len(gemini.calls) == 1


async def test_irrelevant_message_marked_not_useful(db):
    gemini = FakeGemini(result=ExtractionResult(is_useful=False))
    processor = MessageProcessor(gemini, db, keywords=[], min_message_length=8)

    handled = await processor.process(make_message(raw_text="lol good morning everyone!!"))

    assert handled is True
    useful = await db.search(is_useful=True)
    not_useful = await db.search(is_useful=False)
    assert useful == []
    assert len(not_useful) == 1


async def test_gemini_failure_stores_raw_message_without_crashing(db):
    gemini = FakeGemini(raise_error=True)
    processor = MessageProcessor(gemini, db, keywords=[], min_message_length=8)

    handled = await processor.process(make_message())

    assert handled is False
    stats = await db.get_stats()
    assert stats["total_messages"] == 1
    assert stats["total_useful_extractions"] == 0


def test_content_hash_is_stable_and_case_insensitive():
    a = compute_content_hash("Hello   World")
    b = compute_content_hash("hello world")
    c = compute_content_hash("goodbye world")
    assert a == b
    assert a != c
