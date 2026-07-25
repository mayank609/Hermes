"""
Gemini API wrapper responsible for turning a raw Telegram message into the
structured ExtractionResult schema.

Uses Google's `google-genai` SDK with:
  - JSON-constrained structured output (response_schema=ExtractionResult),
    so we get a validated Pydantic object back instead of hand-parsing text.
  - A bounded semaphore to cap concurrent requests (rate-limit friendly).
  - tenacity-based retry with exponential backoff for transient failures
    (429 rate limits, 5xx server errors, network hiccups). Non-retryable
    errors (bad API key, invalid request) fail fast.
"""

from __future__ import annotations

import json
import logging

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import ValidationError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.models import ExtractionResult

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an information-extraction engine for a Telegram monitoring \
agent. You will be given the raw text of a single Telegram group message. \
Your job is to decide whether the message contains useful, actionable \
information and, if so, extract it into a structured JSON object.

Categories of useful information include: Job, Internship, Hackathon, \
AI News, Event, Funding, Open Source, Research Paper, Deadline, \
Important Link, Contact, Stock Tip.

Rules:
- Return ONLY the JSON object described by the response schema. No prose, \
no markdown fences, no explanation.
- If the message is useful, set "is_useful" to true, choose the single \
best-fitting "category", and fill in every field you can confidently infer \
from the text. Leave fields you cannot infer as null (or an empty list for \
list fields). Do not invent information that is not present or strongly \
implied in the message.
- "title" should be a short human-readable headline (max ~12 words).
- "summary" should be 1-3 sentences summarizing what the message offers.
- "important_links" should include every URL mentioned, including \
"apply_link" if present.
- If the message is spam, small talk, a greeting, an emoji-only reaction, \
or otherwise has no extractable value, return exactly: {"is_useful": false}

Stock/share-market tips (category "Stock Tip"):
If the message is a stock market recommendation, trade call, or share tip \
(e.g. "Buy RELIANCE above 2450, target 2600, SL 2400"), set category to \
"Stock Tip" and additionally fill in:
- "stock_name": the company name if mentioned (e.g. "Reliance Industries").
- "stock_symbol": the ticker/symbol if mentioned (e.g. "RELIANCE", "TCS").
- "exchange": the exchange if evident from context (e.g. "NSE", "BSE", \
"NASDAQ"); leave null if not stated or implied.
- "action": one of "Buy", "Sell", or "Hold".
- "entry_price": the entry/current price or range mentioned, as text \
(e.g. "2450" or "2440-2460").
- "target_price": the target price mentioned, as text.
- "stop_loss": the stop-loss price mentioned, as text.
- "timeframe": the holding horizon if mentioned (e.g. "Intraday", \
"Short-term", "1 Week", "Long-term").
- "source": the analyst, channel, or person the tip is attributed to, if \
named in the message (e.g. "as per XYZ Research").
Leave any of these null if the message doesn't mention them. These nine \
fields only apply to "Stock Tip" messages; leave them null for every other \
category.
"""


def _is_retryable(exc: BaseException) -> bool:
    """Decide whether an exception from the Gemini SDK is worth retrying."""
    if isinstance(exc, (json.JSONDecodeError, ValidationError)):
        return True
    if isinstance(exc, genai_errors.ClientError):
        # 4xx: only retry rate limiting (429) and request timeouts (408).
        code = getattr(exc, "code", None)
        return code in (408, 429)
    if isinstance(exc, genai_errors.ServerError):
        # 5xx: always worth a retry.
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return False


class GeminiExtractionError(RuntimeError):
    """Raised when Gemini fails to produce a usable extraction after retries."""


class GeminiExtractor:
    def __init__(
        self,
        api_key: str,
        model_name: str,
        max_retries: int = 5,
        timeout_seconds: float = 60.0,
        max_concurrent_requests: int = 3,
        temperature: float = 0.2,
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model_name = model_name
        self._max_retries = max(1, max_retries)
        self._temperature = temperature
        self._http_options = types.HttpOptions(timeout=int(timeout_seconds * 1000))
        self._semaphore = None  # created lazily inside the running event loop
        self._max_concurrent_requests = max_concurrent_requests

    def _get_semaphore(self):
        import asyncio

        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent_requests)
        return self._semaphore

    async def extract(self, message_text: str) -> ExtractionResult:
        """Extract structured information from a single message's text.

        Raises GeminiExtractionError if extraction fails after all retries.
        """
        semaphore = self._get_semaphore()

        @retry(
            reraise=True,
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception(_is_retryable),
        )
        async def _call() -> ExtractionResult:
            async with semaphore:
                response = await self._client.aio.models.generate_content(
                    model=self._model_name,
                    contents=message_text,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=ExtractionResult,
                        temperature=self._temperature,
                        http_options=self._http_options,
                    ),
                )
            return self._parse_response(response)

        try:
            return await _call()
        except Exception as exc:  # noqa: BLE001 - convert to a domain error at the boundary
            logger.error("Gemini extraction failed after retries: %s", exc)
            raise GeminiExtractionError(str(exc)) from exc

    @staticmethod
    def _parse_response(response) -> ExtractionResult:  # noqa: ANN001
        # The SDK auto-parses into the pydantic model we passed as
        # response_schema when it can. Fall back to manual JSON parsing if
        # `.parsed` isn't populated (e.g. older SDK versions or edge cases).
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, ExtractionResult):
            return parsed

        text = getattr(response, "text", None)
        if not text:
            raise GeminiExtractionError("Gemini returned an empty response")

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()

        data = json.loads(cleaned)
        return ExtractionResult.model_validate(data)
