"""
Export helpers for turning stored extractions into CSV / Excel files.

Kept separate from database.py so the export format can evolve (new
columns, different sheet layout) without touching persistence logic, and so
both the REST API and any future CLI/script can reuse the same code.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import pandas as pd

EXPORT_COLUMNS = [
    "id",
    "group_name",
    "sender_name",
    "message_date",
    "category",
    "title",
    "summary",
    "company",
    "role",
    "location",
    "deadline",
    "apply_link",
    "contact",
    "important_links",
    "skills",
    "tags",
    "is_useful",
    "raw_text",
]


def _to_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for record in records:
        row = {col: record.get(col) for col in EXPORT_COLUMNS}
        for list_field in ("important_links", "skills", "tags"):
            value = row.get(list_field)
            if isinstance(value, list):
                row[list_field] = "; ".join(value)
        rows.append(row)
    return pd.DataFrame(rows, columns=EXPORT_COLUMNS)


def export_to_csv_bytes(records: list[dict[str, Any]]) -> bytes:
    """Serialize records to CSV bytes (UTF-8, with header row)."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        row = dict(record)
        for list_field in ("important_links", "skills", "tags"):
            value = row.get(list_field)
            if isinstance(value, list):
                row[list_field] = "; ".join(value)
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def export_to_excel_bytes(records: list[dict[str, Any]]) -> bytes:
    """Serialize records to an .xlsx workbook (single sheet), returned as bytes."""
    df = _to_dataframe(records)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Extractions")
        worksheet = writer.sheets["Extractions"]
        for idx, column in enumerate(df.columns, start=1):
            max_len = max(
                [len(str(column))] + [len(str(v)) for v in df[column].astype(str).tolist()]
            )
            worksheet.column_dimensions[worksheet.cell(row=1, column=idx).column_letter].width = min(
                max(12, max_len + 2), 60
            )
    return buffer.getvalue()
