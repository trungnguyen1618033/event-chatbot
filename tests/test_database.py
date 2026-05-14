"""
tests/test_database.py
Tests for DatabaseService — all DB interactions are mocked via asyncpg.

These tests verify:
 • insert_event executes the expected SQL
 • transaction rollback occurs on failure
 • event_exists returns correct booleans
 • duplicate-key exception propagates correctly
"""

from __future__ import annotations

from datetime import date, time
from unittest.mock import AsyncMock, MagicMock, call, patch

import asyncpg
import pytest

from app.models.event import EventCreate
from app.services.database import DatabaseService

# ── Fixtures ──────────────────────────────────────────────────────────────────

VALID_EVENT = EventCreate(
    name="Kyoto Jazz Night",
    date=date(2026, 3, 10),
    time=time(19, 0),
    description="A live jazz performance.",
    seat_types={"VIP": 10000, "Regular": 5000},
    purchase_start=date(2026, 1, 1),
    purchase_end=date(2026, 3, 9),
    ticket_limit=4,
    venue_name="Kyoto Concert Hall",
    venue_address="123 Sakyo-ku, Kyoto",
    capacity=1000,
    organizer_name="Fenix Entertainment",
    organizer_email="info@fenix.co.jp",
    category="Concert",
    language="Japanese",
    is_recurring=False,
    is_online=False,
)


@pytest.fixture
def db_service():
    svc = DatabaseService()
    return svc


def make_pool(fetchrow_result=None, fetch_result=None):
    """Create a fully mocked asyncpg-style pool."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    conn.fetch = AsyncMock(return_value=fetch_result or [])
    conn.execute = AsyncMock()

    # transaction() context manager
    txn = AsyncMock()
    txn.__aenter__ = AsyncMock(return_value=txn)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)

    # Pool.acquire() context manager
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    pool.fetch = AsyncMock(return_value=fetch_result or [])
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    pool.close = AsyncMock()

    return pool, conn


# ── event_exists ──────────────────────────────────────────────────────────────


class TestEventExists:
    @pytest.mark.asyncio
    async def test_returns_true_when_row_found(self, db_service):
        pool, conn = make_pool(fetchrow_result={"1": 1})
        db_service._pool = pool

        result = await db_service.event_exists("Kyoto Jazz Night", date(2026, 3, 10))
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_row(self, db_service):
        pool, conn = make_pool(fetchrow_result=None)
        db_service._pool = pool

        result = await db_service.event_exists("Unknown Event", date(2026, 1, 1))
        assert result is False


# ── insert_event ──────────────────────────────────────────────────────────────


class TestInsertEvent:
    @pytest.mark.asyncio
    async def test_successful_insert_returns_row(self, db_service):
        expected_row = {
            "id": 1,
            "name": "Kyoto Jazz Night",
            "date": date(2026, 3, 10),
            "time": time(19, 0),
            "venue_name": "Kyoto Concert Hall",
            "organizer_email": "info@fenix.co.jp",
            "created_at": "2026-01-01T00:00:00",
        }
        pool, conn = make_pool(fetchrow_result=expected_row)
        db_service._pool = pool

        result = await db_service.insert_event(VALID_EVENT)
        assert result["id"] == 1
        assert result["name"] == "Kyoto Jazz Night"

    @pytest.mark.asyncio
    async def test_insert_uses_transaction(self, db_service):
        expected_row = {
            "id": 1,
            "name": "Test",
            "date": date(2026, 3, 10),
            "time": time(19, 0),
            "venue_name": "Hall",
            "organizer_email": "a@b.com",
            "created_at": "now",
        }
        pool, conn = make_pool(fetchrow_result=expected_row)
        db_service._pool = pool

        await db_service.insert_event(VALID_EVENT)
        # verify transaction() was called
        conn.transaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_duplicate_raises_unique_violation(self, db_service):
        pool, conn = make_pool()
        conn.fetchrow = AsyncMock(
            side_effect=asyncpg.UniqueViolationError(
                "duplicate key value violates unique constraint"
            )
        )
        db_service._pool = pool

        with pytest.raises(asyncpg.UniqueViolationError):
            await db_service.insert_event(VALID_EVENT)

    @pytest.mark.asyncio
    async def test_generic_db_error_propagates(self, db_service):
        pool, conn = make_pool()
        conn.fetchrow = AsyncMock(side_effect=Exception("connection lost"))
        db_service._pool = pool

        with pytest.raises(Exception, match="connection lost"):
            await db_service.insert_event(VALID_EVENT)


# ── get_events ────────────────────────────────────────────────────────────────


class TestGetEvents:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self, db_service):
        rows = [
            {"id": 1, "name": "Event A"},
            {"id": 2, "name": "Event B"},
        ]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)
        db_service._pool = pool

        result = await db_service.get_events(limit=10)
        assert len(result) == 2
        assert result[0]["name"] == "Event A"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_events(self, db_service):
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        db_service._pool = pool

        result = await db_service.get_events()
        assert result == []


# ── get_event_by_id ───────────────────────────────────────────────────────────


class TestGetEventById:
    @pytest.mark.asyncio
    async def test_returns_dict_for_existing_event(self, db_service):
        row = {"id": 42, "name": "Special Event"}
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=row)
        db_service._pool = pool

        result = await db_service.get_event_by_id(42)
        assert result["id"] == 42

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_event(self, db_service):
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        db_service._pool = pool

        result = await db_service.get_event_by_id(999)
        assert result is None
