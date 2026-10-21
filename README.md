# İETT Telegram Alerts

A shared, open source Telegram bot that watches temporary service announcements on İETT line pages.
Subscribe to **59RH** or any other supported line and receive new cancellation, delay, diversion,
closure, and timetable notices. Controls are available in Turkish and English; announcements retain
İETT’s original Turkish wording.

The bot is an independent community project, not an official İETT service. It can only forward notices
İETT publishes. A notice published and removed between checks can be missed. Its **first detected**
timestamp is not its publication time. A disappearing notice never means a bus has been reinstated.

## Run with Docker Compose

1. Create a bot with [@BotFather](https://t.me/BotFather) and obtain its token.
2. Copy `.env.example` to `.env`. Set `BOT_TOKEN` and a long random `POSTGRES_PASSWORD` containing
   letters and numbers. Keep `.env` private. Compose supplies the internal database URL automatically.
3. On an always-on server with Docker and the Compose plugin:

   ```sh
   docker compose up -d --build
   docker compose logs -f bot
   ```

4. Open your bot’s Telegram link and send `/start`. Add **59RH** through `/lines` for the pilot.

Only **one bot process per token/database** should run. A database advisory lock prevents duplicate
instances sharing this database; Telegram also requires a single long-polling receiver per token.
No public webhook, domain, or inbound port is required. The database uses a persistent named volume
and is not published to the network. Migrations run automatically on startup.

## Commands

| Command | Purpose |
| --- | --- |
| `/start` | Choose a language and start onboarding |
| `/lines` | Search, follow, or remove lines using buttons |
| `/lines 59RH` | Search directly for a line |
| `/settings` | Change language and per-line notification hours |
| `/settings en` or `/settings tr` | Set the control language |
| `/status` | Show captured notices and last successful source check |
| `/pause` / `/resume` | Pause or resume delivery; monitoring continues |
| `/delete_me` | Confirm deletion of personal data |
| `/cancel` | Cancel the current interaction |
| `/help` | Show command help |

Every line defaults to every day, all day. For weekday commuting, use the **Hours** button or:

```text
/settings 59RH 1,2,3,4,5 07:00-10:00,15:00-19:00
```

Days run from **1 = Monday** to **7 = Sunday**, always in `Europe/Istanbul`. Up to four windows are
supported. End times are exclusive. An overnight Monday `22:00-02:00` window also includes Tuesday
before 02:00. Use `/settings 59RH all` to restore all-day notifications, or
`/settings 59RH 1,2,3,4,5 all` for all hours on weekdays.

Outside selected hours or while paused, collection and saving continue. At the next allowed time,
only notices still present in a fresh successful response are newly queued. Alerts already queued
are retained even if İETT removes their notice. Delivery rechecks notification hours and pause status.
Unchanged announcements are not repeated daily. New subscribers receive current notices within their
allowed hours. An edited announcement is delivered as new content.

## How capture works

The source adapter uses the same public requests as [İETT’s line pages](https://iett.istanbul/RouteDetail?hkod=59RH):

- `GET /tr/RouteStation/GetSearchItems?key=59RH&langid=1` validates exact line codes and names.
- `GET /tr/RouteStation/GetLineStatus?rcode=59RH` returns current announcement strings.

These endpoints are undocumented and may change. They are isolated in `iett.py`. Production collection
uses direct JSON requests and does not need a paid scraping service, AI service, or İETT account.

Each distinct subscribed line is polled once per cycle regardless of subscriber count, including when
subscribers mute alerts. Default checks target 60 seconds, with ±10% jitter and two concurrent
requests. Under heavy load or upstream throttling the interval can grow. Failed requests back off up
to about 15 minutes and honor upstream `Retry-After` delays. Empty JSON arrays are successful checks;
invalid JSON, HTTP errors, and unexpected shapes do not clear saved data.

Pipe-separated announcements are split, Unicode/whitespace is normalized for fingerprints, and order
is ignored. Original text is retained. Two consecutive successful checks without a notice confirm its
removal; a later return creates a new occurrence. Failures do not count as absence. First/last seen,
removal times, and successful raw snapshots are saved. Eligible alerts are queued in the **same
transaction** as capture, protecting against a crash between saving and notification creation.

The PostgreSQL outbox survives restarts, tracks individual message parts, and limits automated delivery
to 20 messages/second globally and roughly one per second per chat. Telegram 429 responses are retried
after the requested delay. Blocked users stop receiving alerts. Delivery is **at least once**: a rare
duplicate is possible if Telegram accepts a message but its response is lost before the receipt is saved.

After ten minutes of unavailable source data, subscribers receive one monitoring outage notice within
their allowed hours. A recovery notice goes only to subscribers who received that outage warning.
No outdated outage warning is newly queued after recovery. Monitoring recovery never claims bus
service recovery. `/status` displays staleness after three minutes by default.

## Local development and tests

Install Python 3.12+ and [uv](https://docs.astral.sh/uv/), then:

```sh
uv sync --extra dev --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Unit tests use recorded public response fixtures and simulated cancellations; they do not contact
Telegram or İETT. Database integration tests require an **isolated test database** and are skipped
when `TEST_DATABASE_URL` is unset:

```sh
docker compose -f compose.test.yaml up -d --wait
TEST_DATABASE_URL=postgresql://iett_test:iett_test@127.0.0.1:55432/iett_test uv run pytest
docker compose -f compose.test.yaml down -v
```

Integration tests create and drop a unique PostgreSQL schema per test. Never supply a production
database as `TEST_DATABASE_URL`. CI runs the full suite against PostgreSQL.

To verify the live public source without a token, database, or Telegram message:

```sh
uv run iett-bot source 59RH
```

To run the application locally, set `.env` with a reachable `DATABASE_URL` and `BOT_TOKEN`, then use
`uv run iett-bot run`. Set `HEALTH_HOST=127.0.0.1` locally. Docker overrides it to `0.0.0.0` within its
unpublished container network. Use `uv run iett-bot migrate` for migrations without starting Telegram.

## Operations and pilot

Internal endpoints on port 8080 expose `/healthz` (worker liveness), `/readyz` (database and source
freshness), and `/metrics` (aggregate Prometheus gauges). They contain no subscriber identifiers and
are not published by Compose. For example:

```sh
docker compose exec bot python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/readyz').read().decode())"
```

Monitor `iett_source_age_seconds`, `iett_pending`, `iett_oldest_pending_seconds`,
`iett_active_outages`, and container uptime. Aggregate metrics are also logged every minute.
Compose restarts crashed processes; configure your server monitor to alert on failures and stale
readiness. Long polling reconnects automatically. Credentials and announcement bodies are not logged.

For the 59RH pilot, check `/status`, observe a real announcement if available, verify detection time
and the official link, and check that disappearance produces no false restoration alert. The automated
simulated-cancellation test covers capture → persistent queue → Telegram send even after source deletion.
The public source had no current 59RH notices during initial inspection; historical cancellations
from before deployment cannot be recovered through this feed.

### Backups and updates

```sh
mkdir -p backups
docker compose exec -T db pg_dump -U iett -d iett -Fc > backups/iett.dump
```

Backups contain personal data. Restrict access and keep them encrypted off the server. For restoration,
stop the bot and restore into a **new, empty** database:

```sh
docker compose stop bot
# Point the database service at a fresh empty volume/database before this command.
docker compose exec -T db pg_restore -U iett -d iett --no-owner < backups/iett.dump
docker compose up -d bot
```

Before an update, take and verify a backup. Pull the code and run `docker compose up -d --build`.
Migrations are forward-only and transactional. Roll back application code only if compatible with the
installed schema; otherwise restore the pre-update backup into a fresh database. Test restoration
periodically. Deleting a Docker volume deletes the database; ordinary container rebuilds preserve it.

## Privacy, retention, and configuration

The bot operates in private chats. It stores Telegram chat/user ID, language, pause/block state,
selected lines, notification hours, and delivery receipts. It does not require a phone number,
location, school, or travel destination. `/delete_me` removes personal records and queued alerts;
shared public source history remains. A message already in flight cannot be recalled. Operators
must manage access to the database and backups; expired backup copies should be removed on a defined
retention schedule, and restoring a backup can restore data that was subsequently deleted.

Removed announcements and snapshots are retained for 90 days by default, with cleanup every six hours.
Active notices and their delivery receipts remain while current to avoid repeat alerts. Unsent queued
alerts and their referenced records remain until delivery or subscription deletion. Retention therefore
does not discard a previously queued alert merely because İETT deleted the source notice.

All configuration is documented in `.env.example`; key settings include polling interval/concurrency,
source timeout, staleness threshold, outage threshold, retention, and a default 20-line subscription
limit per person. A single server/database is the supported deployment for this release. Keep your bot
token, database credentials, `.env`, dumps, and production data out of Git.

## Contributing and license

Run the full test suite and lint checks before submitting changes. Keep source-format changes within
the adapter and add anonymized public fixtures. Do not add credentials or captured Telegram updates.
See [CONTRIBUTING.md](CONTRIBUTING.md) and [LICENSE](LICENSE). Licensed under MIT.
