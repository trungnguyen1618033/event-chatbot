-- Drop existing objects (safe for dev resets)
DROP TABLE IF EXISTS events CASCADE;
DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;


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

    CONSTRAINT uq_event_name_date UNIQUE (name, date),

    CONSTRAINT chk_purchase_period CHECK (purchase_end >= purchase_start),
    CONSTRAINT chk_purchase_before_event CHECK (purchase_end <= date)
);


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


CREATE INDEX idx_events_date       ON events (date);
CREATE INDEX idx_events_category   ON events (category);
CREATE INDEX idx_events_organizer  ON events (organizer_email);
CREATE INDEX idx_events_created_at ON events (created_at DESC);


SELECT 'Schema created successfully' AS status;
