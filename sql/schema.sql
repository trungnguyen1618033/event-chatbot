-- =============================================================================
-- Event Chatbot — Database Schema
-- Run: psql -U postgres -d event_chatbot -f schema.sql
-- =============================================================================

-- Drop existing objects (safe for dev resets)
DROP TABLE IF EXISTS events CASCADE;
DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;

-- =============================================================================
-- Events table
-- =============================================================================
CREATE TABLE events (
    id                   SERIAL PRIMARY KEY,
    name                 VARCHAR(255)  NOT NULL,
    date                 DATE          NOT NULL,
    time                 TIME          NOT NULL,
    description          TEXT,
    seat_types           JSONB         NOT NULL,
    purchase_start       DATE          NOT NULL,
    purchase_end         DATE          NOT NULL,
    ticket_limit         INTEGER       NOT NULL CHECK (ticket_limit > 0),
    venue_name           VARCHAR(255)  NOT NULL,
    venue_address        VARCHAR(255)  NOT NULL,
    capacity             INTEGER       NOT NULL CHECK (capacity > 0),
    organizer_name       VARCHAR(255)  NOT NULL,
    organizer_email      VARCHAR(255)  NOT NULL,
    category             VARCHAR(100)  NOT NULL,
    language             VARCHAR(50)   NOT NULL DEFAULT 'English',
    is_recurring         BOOLEAN       NOT NULL DEFAULT FALSE,
    recurrence_frequency VARCHAR(50),
    is_online            BOOLEAN       NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMP     NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMP     NOT NULL DEFAULT NOW(),

    -- Prevent duplicate events (same name + date)
    CONSTRAINT uq_event_name_date UNIQUE (name, date),

    -- Purchase period must be valid
    CONSTRAINT chk_purchase_period CHECK (purchase_end >= purchase_start),
    CONSTRAINT chk_purchase_before_event CHECK (purchase_end <= date)
);

-- =============================================================================
-- Auto-update updated_at on row modifications
-- =============================================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON events
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- Indexes for common query patterns
-- =============================================================================
CREATE INDEX idx_events_date       ON events (date);
CREATE INDEX idx_events_category   ON events (category);
CREATE INDEX idx_events_organizer  ON events (organizer_email);
CREATE INDEX idx_events_created_at ON events (created_at DESC);

-- =============================================================================
-- Sample seed data (optional — uncomment to populate)
-- =============================================================================
/*
INSERT INTO events (
    name, date, time, description, seat_types, purchase_start, purchase_end,
    ticket_limit, venue_name, venue_address, capacity,
    organizer_name, organizer_email, category, language, is_recurring, is_online
) VALUES (
    'Kyoto Jazz Night',
    '2026-03-10',
    '19:00:00',
    'A live jazz performance in Kyoto.',
    '{"VIP": 10000, "Regular": 5000, "Standing": 3000}'::jsonb,
    '2026-01-01',
    '2026-03-09',
    4,
    'Kyoto Concert Hall',
    '123 Sakyo-ku, Kyoto',
    1000,
    'Fenix Entertainment',
    'info@fenix.co.jp',
    'Concert',
    'Japanese',
    FALSE,
    FALSE
);
*/

-- Verify
SELECT 'Schema created successfully' AS status;
