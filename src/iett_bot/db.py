import json
from datetime import datetime, timedelta
from importlib.resources import files

import asyncpg

from iett_bot.domain import Line, Snapshot, fingerprint
from iett_bot.i18n import incident_messages, notice_messages
from iett_bot.schedule import Schedule


class SubscriptionLimit(ValueError):
    pass


class Store:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> "Store":
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8, command_timeout=30)
        return cls(pool)

    async def close(self) -> None:
        await self.pool.close()

    async def migrate(self) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(59202601)")
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            migrations = files("iett_bot").joinpath("migrations")
            for path in sorted(migrations.iterdir(), key=lambda item: item.name):
                if not path.name.endswith(".sql"):
                    continue
                applied = await conn.fetchval(
                    "SELECT 1 FROM schema_migrations WHERE version=$1", path.name
                )
                if not applied:
                    await conn.execute(path.read_text(encoding="utf-8"))
                    await conn.execute(
                        "INSERT INTO schema_migrations(version) VALUES($1)", path.name
                    )

    async def ensure_user(self, chat_id: int, language: str = "tr") -> dict:
        row = await self.pool.fetchrow(
            "INSERT INTO users(chat_id, language) VALUES($1,$2) "
            "ON CONFLICT(chat_id) DO UPDATE SET blocked=FALSE RETURNING *",
            chat_id,
            language if language in ("tr", "en") else "tr",
        )
        return dict(row)

    async def user(self, chat_id: int) -> dict | None:
        row = await self.pool.fetchrow("SELECT * FROM users WHERE chat_id=$1", chat_id)
        return dict(row) if row else None

    async def set_language(self, chat_id: int, language: str) -> None:
        if language not in ("tr", "en"):
            raise ValueError("Unsupported language")
        await self.pool.execute("UPDATE users SET language=$2 WHERE chat_id=$1", chat_id, language)

    async def set_paused(self, chat_id: int, paused: bool) -> None:
        await self.pool.execute("UPDATE users SET paused=$2 WHERE chat_id=$1", chat_id, paused)
        if not paused:
            await self.pool.execute(
                "UPDATE outbox SET next_attempt_at=LEAST(next_attempt_at,now()) "
                "WHERE chat_id=$1 AND status='pending'",
                chat_id,
            )

    async def delete_user(self, chat_id: int) -> None:
        await self.pool.execute("DELETE FROM users WHERE chat_id=$1", chat_id)

    async def subscribe(self, chat_id: int, line: Line, limit: int) -> bool:
        async with self.pool.acquire() as conn, conn.transaction():
            # Serializes quota checks and additions for one subscriber.
            await conn.fetchval("SELECT chat_id FROM users WHERE chat_id=$1 FOR UPDATE", chat_id)
            if await conn.fetchval(
                "SELECT 1 FROM subscriptions WHERE chat_id=$1 AND line_code=$2", chat_id, line.code
            ):
                return False
            count = await conn.fetchval(
                "SELECT count(*) FROM subscriptions WHERE chat_id=$1", chat_id
            )
            if count >= limit:
                raise SubscriptionLimit
            await conn.execute(
                "INSERT INTO lines(code,name) VALUES($1,$2) ON CONFLICT(code) "
                "DO UPDATE SET name=excluded.name, next_poll_at=CASE WHEN lines.failure_count>0 "
                "THEN lines.next_poll_at ELSE LEAST(lines.next_poll_at,now()) END",
                line.code,
                line.name,
            )
            await conn.execute(
                "INSERT INTO subscriptions(chat_id,line_code) VALUES($1,$2)", chat_id, line.code
            )
            return True

    async def unsubscribe(self, chat_id: int, code: str) -> None:
        await self.pool.execute(
            "DELETE FROM subscriptions WHERE chat_id=$1 AND line_code=$2", chat_id, code
        )

    async def subscriptions(self, chat_id: int) -> list[dict]:
        return [
            dict(row)
            for row in await self.pool.fetch(
                "SELECT s.*,l.name,l.last_success_at,l.failure_count FROM subscriptions s "
                "JOIN lines l ON l.code=s.line_code WHERE s.chat_id=$1 ORDER BY s.line_code",
                chat_id,
            )
        ]

    async def set_schedule(self, chat_id: int, code: str, schedule: Schedule) -> bool:
        row = await self.pool.fetchval(
            "UPDATE subscriptions SET schedule=$3::jsonb WHERE chat_id=$1 AND line_code=$2 "
            "RETURNING TRUE",
            chat_id,
            code,
            schedule.to_json(),
        )
        # Reconsider pending work immediately after a schedule change.
        await self.pool.execute(
            "UPDATE outbox SET next_attempt_at=LEAST(next_attempt_at,now()) "
            "WHERE chat_id=$1 AND line_code=$2 "
            "AND status='pending'",
            chat_id,
            code,
        )
        return bool(row)

    async def current_notices(self, code: str) -> list[dict]:
        return [
            dict(row)
            for row in await self.pool.fetch(
                "SELECT * FROM notices WHERE line_code=$1 AND removed_at IS NULL "
                "AND missing_count=0 ORDER BY first_seen_at,id",
                code,
            )
        ]

    async def line_state(self, code: str) -> dict | None:
        row = await self.pool.fetchrow("SELECT * FROM lines WHERE code=$1", code)
        return dict(row) if row else None

    async def start_check(self, code: str, now: datetime, lease: timedelta) -> None:
        await self.pool.execute(
            "UPDATE lines SET last_attempt_at=$2,next_poll_at=$2::timestamptz+$3::interval "
            "WHERE code=$1",
            code,
            now,
            lease,
        )

    async def due_lines(self, now: datetime, limit: int, lease: timedelta) -> list[dict]:
        rows = await self.pool.fetch(
            "WITH due AS (SELECT l.code FROM lines l WHERE l.next_poll_at <= $1 "
            "AND EXISTS (SELECT 1 FROM subscriptions s JOIN users u USING(chat_id) "
            "WHERE s.line_code=l.code AND NOT u.blocked) "
            "ORDER BY l.next_poll_at FOR UPDATE SKIP LOCKED LIMIT $2) "
            "UPDATE lines l SET next_poll_at=$1+$3::interval,last_attempt_at=$1 "
            "FROM due WHERE l.code=due.code RETURNING l.*",
            now,
            limit,
            lease,
        )
        return [dict(row) for row in rows]

    async def record_snapshot(
        self,
        snapshot: Snapshot,
        next_poll: datetime,
        freshness: timedelta = timedelta(seconds=180),
    ) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.fetchval(
                "SELECT code FROM lines WHERE code=$1 FOR UPDATE", snapshot.line_code
            )
            await conn.execute(
                "INSERT INTO snapshots(line_code,checked_at,raw) VALUES($1,$2,$3::jsonb)",
                snapshot.line_code,
                snapshot.checked_at,
                json.dumps(snapshot.raw, ensure_ascii=False),
            )
            active = {
                row["fingerprint"]: row
                for row in await conn.fetch(
                    "SELECT * FROM notices WHERE line_code=$1 AND removed_at IS NULL",
                    snapshot.line_code,
                )
            }
            seen = set()
            for text in snapshot.notices:
                key = fingerprint(text)
                if key in seen:
                    continue
                seen.add(key)
                if key in active:
                    await conn.execute(
                        "UPDATE notices SET last_seen_at=$2,missing_count=0 WHERE id=$1",
                        active[key]["id"],
                        snapshot.checked_at,
                    )
                else:
                    await conn.execute(
                        "INSERT INTO notices(line_code,fingerprint,original_text,first_seen_at,"
                        "last_seen_at) VALUES($1,$2,$3,$4,$4)",
                        snapshot.line_code,
                        key,
                        text,
                        snapshot.checked_at,
                    )
            for key, row in active.items():
                if key not in seen:
                    await conn.execute(
                        "UPDATE notices SET missing_count=missing_count+1,"
                        "removed_at=CASE WHEN missing_count+1>=2 THEN $2::timestamptz "
                        "ELSE NULL END WHERE id=$1",
                        row["id"],
                        snapshot.checked_at,
                    )
            await conn.execute(
                "UPDATE lines SET last_success_at=$2,failure_since=NULL,failure_count=0,"
                "last_error=NULL,next_poll_at=$3 WHERE code=$1",
                snapshot.line_code,
                snapshot.checked_at,
                next_poll,
            )
            await conn.execute(
                "UPDATE incidents SET ended_at=$2 WHERE line_code=$1 AND ended_at IS NULL",
                snapshot.line_code,
                snapshot.checked_at,
            )
            await self._enqueue_due(conn, snapshot.checked_at, freshness, snapshot.line_code)

    async def record_failure(self, code: str, now: datetime, next_poll: datetime) -> None:
        await self.pool.execute(
            "UPDATE lines SET failure_count=failure_count+1,"
            "failure_since=COALESCE(failure_since,$2),last_error='Source unavailable',"
            "next_poll_at=$3 WHERE code=$1",
            code,
            now,
            next_poll,
        )

    async def open_incidents(self, now: datetime, outage_after: timedelta) -> None:
        await self.pool.execute(
            "WITH unavailable AS (SELECT l.code,"
            "COALESCE(last_success_at,failure_since,created_at) AS started_at FROM lines l "
            "WHERE failure_count>0 AND COALESCE(last_success_at,failure_since,created_at)<=$1 "
            "AND EXISTS (SELECT 1 FROM subscriptions s WHERE s.line_code=l.code) "
            "ORDER BY l.code FOR UPDATE OF l SKIP LOCKED) "
            "INSERT INTO incidents(line_code,started_at) SELECT code,started_at FROM unavailable "
            "ON CONFLICT(line_code) WHERE ended_at IS NULL DO NOTHING",
            now - outage_after,
        )

    async def enqueue_due(self, now: datetime, freshness: timedelta) -> int:
        async with self.pool.acquire() as conn, conn.transaction():
            return await self._enqueue_due(conn, now, freshness)

    async def _enqueue_due(
        self,
        conn: asyncpg.Connection,
        now: datetime,
        freshness: timedelta,
        line_code: str | None = None,
    ) -> int:
        added = 0
        # Only consider undelivered occurrences, rather than re-inserting every receipt.
        rows = await conn.fetch(
            "SELECT s.chat_id,s.schedule,u.language,l.code,l.name,n.* FROM subscriptions s "
            "JOIN users u USING(chat_id) JOIN lines l ON l.code=s.line_code "
            "JOIN notices n ON n.line_code=l.code "
            "LEFT JOIN outbox q ON q.chat_id=s.chat_id AND q.notice_id=n.id "
            "WHERE NOT u.paused AND NOT u.blocked AND n.removed_at IS NULL "
            "AND n.missing_count=0 AND l.last_success_at >= $1 AND q.id IS NULL "
            "AND ($2::text IS NULL OR l.code=$2) ORDER BY l.code,n.id,s.chat_id",
            now - freshness,
            line_code,
        )
        for row in rows:
            if not Schedule.from_json(row["schedule"]).is_open(now):
                continue
            result = await conn.fetchval(
                "INSERT INTO outbox(chat_id,line_code,event_key,kind,notice_id,messages,"
                "created_at,next_attempt_at) VALUES($1,$2,$3,'notice',$4,$5::jsonb,$6,$6) "
                "ON CONFLICT DO NOTHING RETURNING id",
                row["chat_id"],
                row["line_code"],
                f"notice:{row['id']}",
                row["id"],
                json.dumps(
                    notice_messages(
                        Line(row["code"], row["name"]),
                        row["original_text"],
                        row["first_seen_at"],
                        row["language"],
                    ),
                    ensure_ascii=False,
                ),
                now,
            )
            added += result is not None
        events = await conn.fetch(
            "SELECT s.chat_id,s.schedule,u.language,l.code,l.name,i.*,"
            "CASE WHEN i.ended_at IS NULL THEN 'outage' ELSE 'recovery' END AS kind "
            "FROM subscriptions s JOIN users u USING(chat_id) "
            "JOIN lines l ON l.code=s.line_code JOIN incidents i ON i.line_code=l.code "
            "WHERE NOT u.paused AND NOT u.blocked AND ($1::text IS NULL OR l.code=$1) "
            "AND (i.ended_at IS NULL OR EXISTS (SELECT 1 FROM outbox old "
            "WHERE old.chat_id=s.chat_id AND old.incident_id=i.id AND old.kind='outage' "
            "AND old.status='sent')) "
            "AND NOT EXISTS (SELECT 1 FROM outbox q WHERE q.chat_id=s.chat_id "
            "AND q.incident_id=i.id AND q.kind=CASE WHEN i.ended_at IS NULL "
            "THEN 'outage' ELSE 'recovery' END) ORDER BY l.code,i.id,s.chat_id",
            line_code,
        )
        for row in events:
            if not Schedule.from_json(row["schedule"]).is_open(now):
                continue
            kind = row["kind"]
            result = await conn.fetchval(
                "INSERT INTO outbox(chat_id,line_code,event_key,kind,incident_id,messages,"
                "created_at,next_attempt_at) VALUES($1,$2,$3,$4,$5,$6::jsonb,$7,$7) "
                "ON CONFLICT DO NOTHING RETURNING id",
                row["chat_id"],
                row["line_code"],
                f"incident:{row['id']}:{kind}",
                kind,
                row["id"],
                json.dumps(
                    incident_messages(
                        Line(row["code"], row["name"]),
                        kind,
                        row["ended_at"] or row["started_at"],
                        row["language"],
                    ),
                    ensure_ascii=False,
                ),
                now,
            )
            added += result is not None
        return added

    async def claim_delivery(self, now: datetime) -> dict | None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE outbox SET status='pending',leased_until=NULL "
                "WHERE status='sending' AND leased_until <= $1",
                now,
            )
            rows = await conn.fetch(
                "SELECT q.*,s.schedule FROM outbox q JOIN users u USING(chat_id) "
                "JOIN subscriptions s ON s.chat_id=q.chat_id AND s.line_code=q.line_code "
                "WHERE q.status='pending' AND q.next_attempt_at <= $1 "
                "AND NOT u.paused AND NOT u.blocked ORDER BY q.next_attempt_at,q.id "
                "FOR UPDATE OF q SKIP LOCKED LIMIT 100",
                now,
            )
            for row in rows:
                schedule = Schedule.from_json(row["schedule"])
                if not schedule.is_open(now):
                    await conn.execute(
                        "UPDATE outbox SET next_attempt_at=$2 WHERE id=$1",
                        row["id"],
                        schedule.next_open(now),
                    )
                    continue
                await conn.execute(
                    "UPDATE outbox SET status='sending',leased_until=$2 WHERE id=$1",
                    row["id"],
                    now + timedelta(minutes=2),
                )
                job = dict(row)
                job["messages"] = json.loads(job["messages"])
                return job
        return None

    async def delivery_allowed(self, job_id: int, now: datetime) -> bool:
        row = await self.pool.fetchrow(
            "SELECT s.schedule FROM outbox q JOIN users u USING(chat_id) "
            "JOIN subscriptions s ON s.chat_id=q.chat_id AND s.line_code=q.line_code "
            "WHERE q.id=$1 AND q.status='sending' AND NOT u.paused AND NOT u.blocked",
            job_id,
        )
        return row is not None and Schedule.from_json(row["schedule"]).is_open(now)

    async def delivery_part_sent(self, job_id: int, now: datetime) -> None:
        await self.pool.execute(
            "UPDATE outbox SET next_part=next_part+1,leased_until=NULL,last_error=NULL,"
            "status=CASE WHEN next_part+1>=jsonb_array_length(messages) THEN 'sent' "
            "ELSE 'pending' END,sent_at=CASE WHEN next_part+1>=jsonb_array_length(messages) "
            "THEN $2::timestamptz ELSE NULL END,next_attempt_at=$2 "
            "WHERE id=$1 AND status='sending'",
            job_id,
            now,
        )

    async def retry_delivery(self, job_id: int, next_attempt: datetime, reason: str) -> None:
        await self.pool.execute(
            "UPDATE outbox SET status='pending',attempts=attempts+1,leased_until=NULL,"
            "next_attempt_at=$2,last_error=$3 WHERE id=$1 AND status='sending'",
            job_id,
            next_attempt,
            reason[:80],
        )

    async def discard_delivery(self, job_id: int) -> None:
        await self.pool.execute(
            "UPDATE outbox SET status='discarded',leased_until=NULL,"
            "last_error='Permanent delivery error' WHERE id=$1",
            job_id,
        )

    async def block_user(self, chat_id: int) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("UPDATE users SET blocked=TRUE WHERE chat_id=$1", chat_id)
            await conn.execute(
                "UPDATE outbox SET status='discarded',leased_until=NULL "
                "WHERE chat_id=$1 AND status IN ('pending','sending')",
                chat_id,
            )

    async def cleanup(self, cutoff: datetime) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM snapshots WHERE checked_at < $1", cutoff)
            await conn.execute(
                "DELETE FROM notices n WHERE removed_at < $1 AND NOT EXISTS "
                "(SELECT 1 FROM outbox q WHERE q.notice_id=n.id "
                "AND q.status IN ('pending','sending'))",
                cutoff,
            )
            await conn.execute(
                "DELETE FROM incidents i WHERE ended_at < $1 AND NOT EXISTS "
                "(SELECT 1 FROM outbox q WHERE q.incident_id=i.id "
                "AND q.status IN ('pending','sending'))",
                cutoff,
            )

    async def metrics(self, now: datetime) -> dict:
        row = await self.pool.fetchrow(
            "SELECT (SELECT count(DISTINCT s.line_code) FROM subscriptions s JOIN users u "
            "USING(chat_id) WHERE NOT u.blocked) AS subscribed_lines,"
            "(SELECT count(*) FROM outbox WHERE status IN ('pending','sending')) AS pending,"
            "(SELECT EXTRACT(EPOCH FROM ($1-min(created_at))) FROM outbox "
            "WHERE status IN ('pending','sending')) AS oldest_pending_seconds,"
            "(SELECT EXTRACT(EPOCH FROM ($1-min(COALESCE(last_success_at,l.created_at)))) "
            "FROM lines l WHERE EXISTS(SELECT 1 FROM subscriptions s JOIN users u "
            "USING(chat_id) WHERE s.line_code=l.code AND NOT u.blocked)) AS source_age_seconds,"
            "(SELECT count(*) FROM incidents WHERE ended_at IS NULL) AS active_outages,"
            "(SELECT count(*) FROM outbox q JOIN users u USING(chat_id) "
            "WHERE q.status='pending' AND NOT u.paused AND NOT u.blocked "
            "AND q.next_attempt_at < $1 - interval '5 minutes') AS overdue_deliveries",
            now,
        )
        return {key: float(value or 0) for key, value in dict(row).items()}
