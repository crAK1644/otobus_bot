CREATE TABLE users (
    chat_id BIGINT PRIMARY KEY,
    language TEXT NOT NULL DEFAULT 'tr' CHECK (language IN ('tr', 'en')),
    paused BOOLEAN NOT NULL DEFAULT FALSE,
    blocked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE lines (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    next_poll_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_attempt_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    failure_since TIMESTAMPTZ,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE TABLE subscriptions (
    chat_id BIGINT NOT NULL REFERENCES users ON DELETE CASCADE,
    line_code TEXT NOT NULL REFERENCES lines,
    schedule JSONB NOT NULL DEFAULT '{"days":[0,1,2,3,4,5,6],"windows":[]}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (chat_id, line_code)
);

CREATE TABLE snapshots (
    id BIGSERIAL PRIMARY KEY,
    line_code TEXT NOT NULL REFERENCES lines,
    checked_at TIMESTAMPTZ NOT NULL,
    raw JSONB NOT NULL
);
CREATE INDEX snapshots_retention ON snapshots (checked_at);

CREATE TABLE notices (
    id BIGSERIAL PRIMARY KEY,
    line_code TEXT NOT NULL REFERENCES lines,
    fingerprint TEXT NOT NULL,
    original_text TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    removed_at TIMESTAMPTZ,
    missing_count INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX notices_current ON notices (line_code, fingerprint) WHERE removed_at IS NULL;
CREATE INDEX notices_retention ON notices (removed_at) WHERE removed_at IS NOT NULL;

CREATE TABLE incidents (
    id BIGSERIAL PRIMARY KEY,
    line_code TEXT NOT NULL REFERENCES lines,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX incidents_current ON incidents (line_code) WHERE ended_at IS NULL;

CREATE TABLE outbox (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    line_code TEXT NOT NULL,
    event_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('notice', 'outage', 'recovery')),
    notice_id BIGINT REFERENCES notices ON DELETE CASCADE,
    incident_id BIGINT REFERENCES incidents ON DELETE CASCADE,
    messages JSONB NOT NULL,
    next_part INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'discarded')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    leased_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ,
    last_error TEXT,
    FOREIGN KEY (chat_id, line_code) REFERENCES subscriptions ON DELETE CASCADE,
    UNIQUE (chat_id, line_code, event_key)
);
CREATE INDEX outbox_pending ON outbox (next_attempt_at, id)
    WHERE status IN ('pending', 'sending');
