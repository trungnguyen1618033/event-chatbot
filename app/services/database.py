from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import asyncpg

from app.config import get_settings
from app.models.event import EventCreate

logger = logging.getLogger(__name__)


class DatabaseService:

    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        settings = get_settings()
        self._pool = await asyncpg.create_pool(
            host=settings.db_host,
            port=settings.db_port,
            database=settings.db_name,
            user=settings.db_user,
            password=settings.db_password,
            min_size=2,
            max_size=10,
        )
        logger.info("Database pool created.")

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.close()
            logger.info("Database pool closed.")

    async def create_tables(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS events (
            id                  SERIAL PRIMARY KEY,
            name                VARCHAR(255)  NOT NULL,
            date                DATE          NOT NULL,
            time                TIME          NOT NULL,
            description         TEXT,
            seat_types          JSONB         NOT NULL,
            purchase_start      DATE          NOT NULL,
            purchase_end        DATE          NOT NULL,
            ticket_limit        INTEGER       NOT NULL,
            venue_name          VARCHAR(255)  NOT NULL,
            venue_address       VARCHAR(255)  NOT NULL,
            capacity            INTEGER       NOT NULL,
            organizer_name      VARCHAR(255)  NOT NULL,
            organizer_email     VARCHAR(255)  NOT NULL,
            category            VARCHAR(100)  NOT NULL,
            language            VARCHAR(50)   NOT NULL DEFAULT 'English',
            is_recurring        BOOLEAN       NOT NULL DEFAULT FALSE,
            recurrence_frequency VARCHAR(50),
            is_online           BOOLEAN       NOT NULL DEFAULT FALSE,
            created_at          TIMESTAMP     NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMP     NOT NULL DEFAULT NOW(),
            UNIQUE (name, date)
        );

        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
           NEW.updated_at = NOW();
           RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS set_updated_at ON events;
        CREATE TRIGGER set_updated_at
        BEFORE UPDATE ON events
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        """
        async with self._pool.acquire() as conn:
            await conn.execute(ddl)
        logger.info("Tables ensured.")

    async def event_exists(self, name: str, date: Any) -> bool:
        row = await self._pool.fetchrow(
            "SELECT 1 FROM events WHERE name = $1 AND date = $2",
            name,
            date,
        )
        return row is not None

    async def insert_event(self, event: EventCreate) -> Dict[str, Any]:
        sql = """
        INSERT INTO events (
            name, date, time, description, seat_types,
            purchase_start, purchase_end, ticket_limit,
            venue_name, venue_address, capacity,
            organizer_name, organizer_email,
            category, language, is_recurring, recurrence_frequency, is_online
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18
        )
        RETURNING id, name, date, time, venue_name, organizer_email, created_at;
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    sql,
                    event.name,
                    event.date,
                    event.time,
                    event.description,
                    json.dumps(event.seat_types),
                    event.purchase_start,
                    event.purchase_end,
                    event.ticket_limit,
                    event.venue_name,
                    event.venue_address,
                    event.capacity,
                    event.organizer_name,
                    event.organizer_email,
                    event.category,
                    event.language,
                    event.is_recurring,
                    event.recurrence_frequency,
                    event.is_online,
                )
        return dict(row)

    async def get_events(self, limit: int = 50) -> list:
        rows = await self._pool.fetch(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT $1", limit
        )
        return [dict(r) for r in rows]

    async def get_event_by_id(self, event_id: int) -> Optional[Dict[str, Any]]:
        row = await self._pool.fetchrow("SELECT * FROM events WHERE id = $1", event_id)
        return dict(row) if row else None


db_service = DatabaseService()
