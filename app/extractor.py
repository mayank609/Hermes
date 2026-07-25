"""
Message processing pipeline: raw Telegram message -> dedup/filter checks ->
Gemini extraction -> persisted record.

This module is the glue between telegram_client.py (produces raw messages)
and gemini.py / database.py (turn them into stored, structured data). It
contains no Telegram- or Gemini-SDK-specific code, which keeps it easy to
unit test with fakes.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from app.database import Database, RawMessage
from app.gemini import GeminiExtractionError, GeminiExtractor

logger = logging.getLogger(__name__)


@dataclass
class NewMessage:
    """Input to the pipeline - a message freshly received from Telegram."""

    message_id: int
    group_id: str
    group_name: str
    sender_id: str | None
    sender_name: str | None
    raw_text: str
    message_date: object  # datetime, kept loosely typed to avoid importing datetime twice


def compute_content_hash(text: str) -> str:
    normalized = " ".join(text.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class MessageProcessor:
    """Orchestrates deduplication, keyword filtering, extraction, and storage."""

    def __init__(
        self,
        gemini: GeminiExtractor,
        db: Database,
        keywords: list[str] | None = None,
        min_message_length: int = 8,
    ) -> None:
        self._gemini = gemini
        self._db = db
        self._keywords = [k.lower() for k in (keywords or [])]
        self._min_message_length = min_message_length

    def _passes_keyword_filter(self, text: str) -> bool:
        if not self._keywords:
            return True
        lowered = text.lower()
        return any(keyword in lowered for keyword in self._keywords)

    async def process(self, message: NewMessage) -> bool:
        """Process a single new message end-to-end.

        Returns True if the message was sent to Gemini and stored, False if
        it was skipped (empty, too short, duplicate, or filtered out).
        """
        text = (message.raw_text or "").strip()
        if len(text) < self._min_message_length:
            logger.debug(
                "Skipping message %s/%s: too short (%d chars)",
                message.group_id,
                message.message_id,
                len(text),
            )
            return False

        if await self._db.message_exists(message.group_id, message.message_id):
            logger.debug(
                "Skipping message %s/%s: already processed (duplicate message id)",
                message.group_id,
                message.message_id,
            )
            return False

        content_hash = compute_content_hash(text)
        if await self._db.content_hash_exists(content_hash):
            logger.info(
                "Skipping message %s/%s: duplicate content already seen",
                message.group_id,
                message.message_id,
            )
            return False

        if not self._passes_keyword_filter(text):
            logger.debug(
                "Skipping message %s/%s: no configured keyword matched",
                message.group_id,
                message.message_id,
            )
            return False

        raw = RawMessage(
            message_id=message.message_id,
            group_id=message.group_id,
            group_name=message.group_name,
            sender_id=message.sender_id,
            sender_name=message.sender_name,
            raw_text=text,
            content_hash=content_hash,
            message_date=message.message_date,
        )

        try:
            extraction = await self._gemini.extract(text)
        except GeminiExtractionError:
            logger.exception(
                "Gemini extraction failed permanently for message %s/%s; storing raw only",
                message.group_id,
                message.message_id,
            )
            await self._db.save_extraction(raw, extraction=None, extraction_failed=True)
            return False

        message_pk = await self._db.save_extraction(raw, extraction=extraction)

        if extraction.is_useful:
            logger.info(
                "Extracted [%s] '%s' from %s (message pk=%s)",
                extraction.category,
                extraction.title,
                message.group_name,
                message_pk,
            )
        else:
            logger.debug(
                "Message %s/%s classified as not useful",
                message.group_id,
                message.message_id,
            )
        return True
