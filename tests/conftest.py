from __future__ import annotations

import datetime as dt

import pytest

from app.database import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await database.init()
    yield database
    await database.close()


@pytest.fixture
def fixed_now() -> dt.datetime:
    return dt.datetime(2026, 7, 25, 12, 0, 0)
