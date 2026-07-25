"""
Telethon-based Telegram client that monitors multiple groups for new
messages and hands each one to a callback for processing.

Handles:
  - Resolving group usernames/IDs from config into Telethon entities.
  - Automatic reconnection (Telethon's built-in retry/backoff).
  - FloodWaitError backoff so we never hammer Telegram's rate limits.
  - Graceful handling of per-message processing errors so one bad message
    never takes down the whole listener.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError

from app.config import DATA_DIR, Settings
from app.extractor import NewMessage

logger = logging.getLogger(__name__)

OnMessageCallback = Callable[[NewMessage], Awaitable[None]]


def _parse_group_ref(raw: str) -> str | int:
    """A group ref may be a username ("mygroup" / "@mygroup") or a numeric
    chat ID (including the "-100..." supergroup form). Numeric strings are
    converted to int so Telethon resolves them by ID instead of username."""
    candidate = raw.strip()
    stripped = candidate[1:] if candidate.startswith("-") else candidate
    if stripped.isdigit():
        return int(candidate)
    return candidate


class TelegramWatcher:
    def __init__(self, settings: Settings, on_message: OnMessageCallback) -> None:
        self._settings = settings
        self._on_message = on_message
        self._client = TelegramClient(
            str(DATA_DIR / settings.session_name),
            settings.api_id,
            settings.api_hash,
            connection_retries=None,  # retry forever
            retry_delay=5,
            auto_reconnect=True,
            request_retries=5,
        )

    async def start(self) -> None:
        await self._client.start(phone=self._settings.phone_number)
        logger.info("Connected to Telegram as %s", self._settings.phone_number)

        entities = await self._resolve_groups()
        if not entities:
            raise RuntimeError(
                "None of the configured TELEGRAM_GROUPS could be resolved. "
                "Check that the account is a member of each group and that "
                "usernames/IDs are correct."
            )

        self._client.add_event_handler(self._handle_new_message, events.NewMessage(chats=entities))
        logger.info("Listening for new messages in %d group(s)", len(entities))

        await self._client.run_until_disconnected()

    async def stop(self) -> None:
        await self._client.disconnect()

    async def send_to_saved_messages(self, text: str) -> None:
        """Post a message to the user's own Saved Messages chat - used for
        digests (e.g. the daily stock-tip summary). Best-effort: failures
        are logged, not raised, since a delivery hiccup shouldn't affect
        the rest of the pipeline."""
        try:
            await self._client.send_message("me", text)
        except Exception:  # noqa: BLE001 - digest delivery must never crash the pipeline
            logger.exception("Failed to send digest to Saved Messages")

    async def _resolve_groups(self) -> list:
        entities = []
        for raw in self._settings.telegram_groups:
            ref = _parse_group_ref(raw)
            try:
                entity = await self._client.get_entity(ref)
                entities.append(entity)
                name = getattr(entity, "title", None) or getattr(entity, "username", str(ref))
                logger.info("Resolved group '%s' -> %s", raw, name)
            except (ValueError, RPCError) as exc:
                logger.error("Could not resolve group '%s': %s", raw, exc)
        return entities

    async def _handle_new_message(self, event: events.NewMessage.Event) -> None:
        try:
            await self._dispatch(event)
        except FloodWaitError as exc:
            logger.warning("Hit Telegram flood wait; sleeping %s seconds", exc.seconds)
            await asyncio.sleep(exc.seconds)
        except Exception:  # noqa: BLE001 - never let one bad message kill the listener
            logger.exception("Unhandled error while processing an incoming message")

    async def _dispatch(self, event: events.NewMessage.Event) -> None:
        text = event.raw_text or ""
        if not text.strip():
            return  # media-only message with no caption: nothing to extract

        chat = await event.get_chat()
        group_name = getattr(chat, "title", None) or getattr(chat, "username", None) or str(event.chat_id)

        sender_id = str(event.sender_id) if event.sender_id else None
        sender_name = None
        try:
            sender = await event.get_sender()
            if sender is not None:
                sender_name = getattr(sender, "username", None) or getattr(sender, "first_name", None)
        except RPCError:
            pass  # sender lookup is best-effort; never block processing on it

        message = NewMessage(
            message_id=event.message.id,
            group_id=str(event.chat_id),
            group_name=group_name,
            sender_id=sender_id,
            sender_name=sender_name,
            raw_text=text,
            message_date=event.message.date,
        )
        await self._on_message(message)
