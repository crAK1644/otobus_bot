-- Departures named by a notice, as [[origin fragment, hour], ...]; NULL = not understood (always delivered).
ALTER TABLE notices ADD COLUMN departures JSONB;
-- A rider's departure picks as {"TERMINAL": [hours]}; {} = every departure (the previous behavior).
ALTER TABLE subscriptions ADD COLUMN departures JSONB NOT NULL DEFAULT '{}';
