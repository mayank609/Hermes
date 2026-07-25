"""
Formatting helpers for turning aggregated stock-tip data (as returned by
Database.get_stock_insights) into a human-readable digest - used for the
daily Telegram "Saved Messages" post and the summary file written to
data/summaries/.
"""

from __future__ import annotations

from typing import Any


def format_stock_digest(date_label: str, summary: dict[str, Any]) -> str:
    """Render a stock_insights summary dict as plain text."""
    lines = [f"Stock Tips Digest - {date_label}"]

    total = summary.get("total_tips", 0)
    if total == 0:
        lines.append("No stock tips were captured today.")
        return "\n".join(lines)

    lines.append(
        f"Total tips: {total}  "
        f"(Buy: {summary.get('buy_count', 0)}, "
        f"Sell: {summary.get('sell_count', 0)}, "
        f"Hold: {summary.get('hold_count', 0)})"
    )
    lines.append("")
    lines.append("Top mentioned stocks:")

    for stock in summary.get("top_stocks", []):
        label = stock.get("symbol") or stock.get("name") or "Unknown"
        exchange = f" ({stock['exchange']})" if stock.get("exchange") else ""
        counts = f"mentions: {stock['mentions']} | buy: {stock['buy']} sell: {stock['sell']} hold: {stock['hold']}"
        latest = (
            f"latest call: {stock.get('latest_action') or '-'}"
            f", target: {stock.get('latest_target') or '-'}"
            f", stop-loss: {stock.get('latest_stop_loss') or '-'}"
        )
        lines.append(f"- {label}{exchange} -> {counts} | {latest}")

    return "\n".join(lines)
