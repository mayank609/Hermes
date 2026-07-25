"""
Application entrypoint.

Wires together the Telegram listener, the Gemini-backed extraction
pipeline, the database, the optional FastAPI server, and the optional
daily-summary scheduler, then runs them all concurrently under one
asyncio event loop.

Usage:
    python -m app.main
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import signal

import uvicorn

from app.api import create_app
from app.config import LOG_DIR, SUMMARY_DIR, ensure_data_dirs, get_settings
from app.database import Database
from app.extractor import MessageProcessor
from app.gemini import GeminiExtractor
from app.insights import format_stock_digest
from app.logging_config import setup_logging
from app.telegram_client import TelegramWatcher

logger = logging.getLogger(__name__)


async def _run_api_server(app, host: str, port: int) -> None:
    config = uvicorn.Config(app, host=host, port=port, log_config=None, lifespan="on")
    server = uvicorn.Server(config)
    logger.info("Starting REST API on http://%s:%s", host, port)
    await server.serve()


async def _daily_summary_loop(db: Database, hour: int, watcher: TelegramWatcher) -> None:
    """Sleep until `hour` (UTC) every day, then log + persist a daily digest
    (general categories) and a dedicated stock-tip digest, the latter also
    posted to the user's own Telegram Saved Messages."""
    while True:
        now = dt.datetime.utcnow()
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += dt.timedelta(days=1)
        sleep_seconds = (target - now).total_seconds()
        logger.info("Daily summary scheduled in %.0f minutes", sleep_seconds / 60)
        await asyncio.sleep(sleep_seconds)

        today = dt.datetime.utcnow().date()

        try:
            summary = await db.get_daily_summary(today)
            logger.info(
                "Daily summary for %s: %d useful item(s) across %d categor(y/ies)",
                summary["date"],
                summary["total_useful"],
                len(summary["by_category"]),
            )
            out_path = SUMMARY_DIR / f"{summary['date']}.json"
            out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        except Exception:  # noqa: BLE001 - the scheduler must survive a bad day
            logger.exception("Failed to generate daily summary")

        try:
            start = dt.datetime.combine(today, dt.time.min)
            end = dt.datetime.combine(today, dt.time.max)
            stock_summary = await db.get_stock_insights(start_date=start, end_date=end, limit=10)
            stock_out_path = SUMMARY_DIR / f"stocks-{today.isoformat()}.json"
            stock_out_path.write_text(json.dumps(stock_summary, indent=2, default=str), encoding="utf-8")

            if stock_summary["total_tips"] > 0:
                digest_text = format_stock_digest(today.isoformat(), stock_summary)
                logger.info(
                    "Stock digest for %s: %d tip(s) across %d stock(s)",
                    today.isoformat(),
                    stock_summary["total_tips"],
                    len(stock_summary["top_stocks"]),
                )
                await watcher.send_to_saved_messages(digest_text)
        except Exception:  # noqa: BLE001 - the scheduler must survive a bad day
            logger.exception("Failed to generate stock digest")


async def run() -> None:
    settings = get_settings()
    ensure_data_dirs()
    setup_logging(settings.log_level, LOG_DIR)

    logger.info("Starting Telegram Information Extraction Agent")
    logger.info("Monitoring groups: %s", ", ".join(settings.telegram_groups))
    logger.info("Gemini model: %s", settings.gemini_model)

    db = Database(settings.database_url)
    await db.init()

    gemini = GeminiExtractor(
        api_key=settings.gemini_api_key,
        model_name=settings.gemini_model,
        max_retries=settings.gemini_max_retries,
        timeout_seconds=settings.gemini_timeout_seconds,
        max_concurrent_requests=settings.gemini_max_concurrent_requests,
        temperature=settings.gemini_temperature,
    )

    processor = MessageProcessor(
        gemini=gemini,
        db=db,
        keywords=settings.keywords,
        min_message_length=settings.min_message_length,
    )

    watcher = TelegramWatcher(settings, on_message=processor.process)

    tasks = [asyncio.create_task(watcher.start(), name="telegram-watcher")]

    if settings.run_api:
        api_app = create_app(settings=settings, db=db)
        tasks.append(
            asyncio.create_task(
                _run_api_server(api_app, settings.api_host, settings.api_port), name="api-server"
            )
        )

    if settings.daily_summary_enabled:
        tasks.append(
            asyncio.create_task(
                _daily_summary_loop(db, settings.daily_summary_hour, watcher), name="daily-summary"
            )
        )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_shutdown() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            pass  # signal handlers aren't available on some platforms (e.g. Windows)

    try:
        done, pending = await asyncio.wait(
            [*tasks, asyncio.create_task(stop_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            if task.exception():
                logger.error("Task %s failed: %s", task.get_name(), task.exception())
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await watcher.stop()
        await db.close()
        logger.info("Shutdown complete")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
