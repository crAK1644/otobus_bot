# IETT Notification

<img src="assets/profile.jpg" alt="Yellow bus with a notification bell" width="128" height="128">

A shared, open source Telegram bot that watches temporary service announcements on İETT line pages.
Subscribe to **59RH** or any other supported line and receive new cancellation, delay, diversion,
closure, and timetable notices. Controls are available in Turkish and English; announcements retain
İETT’s original Turkish wording.

## Use the hosted bot

Open [@iett_notification_bot on Telegram](https://t.me/iett_notification_bot), press **Start**, and use `/lines`
to follow your bus lines. For the 59RH pilot, send `/lines 59RH` and select the matching result.
You can follow up to 20 lines: use `/lines` → **Add a line** for each additional line.
After following, the bot immediately checks and replies with the number of announcements or says
there are none. Existing notices are delivered according to your notification hours and pause setting.
Use `/settings` to change language or notification hours. Use `/status` to check announcements for
all your lines now, `/status 59RH` for one followed line, or its **Check now** button to read the full
announcements, even while automatic alerts are paused or outside your notification hours.
Share a line with a one-tap link: `https://t.me/iett_notification_bot?start=59RH` opens the bot and
follows that line as soon as the person presses **Start** (the bot shows each line's link after you
follow it). Line codes with Turkish letters use an encoded form of the same link.
The hosted bot runs on an always-on server; subscribers do not need to install or run this code.
The former `@cmp_visitor_bot` points visitors to the new bot. Existing followers should press **Start**
in the new bot; their saved lines and notification hours remain in the same database.

The bot is an independent community project, not an official İETT service. It can only forward notices
İETT publishes. A notice published and removed between checks can be missed. Its **first detected**
timestamp is not its publication time. A disappearing notice never means a bus has been reinstated.

When moving a deployment to a replacement bot, put the replacement token in `BOT_TOKEN` and the
former token in the optional `LEGACY_BOT_TOKEN`, both in the private `.env` file. The same process
keeps the former bot answering private messages and old buttons with a link to the replacement,
while one shared monitor and delivery worker serve the replacement. Keep the existing database
to preserve settings and history. Each follower must press **Start** in the replacement bot before
it can send them alerts. Leave `LEGACY_BOT_TOKEN` blank for a deployment with only one bot.

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

For a shared always-on server, use the optional resource limits and bounded container logs:

```sh
docker compose -p iett-alerts -f compose.yaml -f compose.production.yaml up -d --build
```

This caps the bot at 512 MB of memory and the database at 768 MB, with half a CPU each. Both services
use a private Docker network and publish no host ports. Add the same `-p` and `-f` options to later
Compose commands for this deployment. Database history still grows with the number of monitored
lines; monitor available disk space and keep off-server backups.

## Commands

| Command | Purpose |
| --- | --- |
| `/start` | Choose a language and start onboarding |
| `/lines` | Search, follow, or remove lines using buttons |
| `/lines 59RH` | Search directly for a line |
| `/settings` | Change language and per-line notification hours |
| `/settings en` or `/settings tr` | Set the control language |
| `/status` | Refresh and show announcements for all followed lines, with last successful check |
| `/status 59RH` | Refresh and show announcements for one followed line |
| `/pause` / `/resume` | Pause or resume delivery; monitoring continues |
| `/privacy` | Show what data is stored, why, and for how long |
| `/delete_me` | Confirm deletion of personal data |
| `/cancel` | Cancel the current interaction |
| `/help` | Show command help |

Every line defaults to every day, all day. For weekday commuting, use the **Notify hours** button or:

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

### Departure picks

Some notices name one trip, for example "HACIOSMAN METRO kalkışlı 07:50 seferi iptal". Use the
**Departures** button on `/lines` to pick a direction and the hours you ride (hours come from the
line's İETT timetable; without one, all 24 are offered). A notice is skipped only when every
departure it names is from a direction you narrowed, at an hour you did not pick. Line-wide notices,
notices the parser does not fully understand, and directions you left alone always arrive, and
`/status` still shows everything. Parsing is rule-based (`parse_departures` in `domain.py`), with
no AI. Nothing picked means every departure.

## How capture works

The source adapter uses the same public requests as [İETT’s line pages](https://iett.istanbul/RouteDetail?hkod=59RH):

- `GET /tr/RouteStation/GetSearchItems?key=59RH&langid=1` validates exact line codes and names.
- `GET /tr/RouteStation/GetLineStatus?rcode=59RH` returns current announcement strings.

These endpoints are undocumented and may change. They are isolated in `iett.py`. Production collection
uses direct JSON requests and does not need a paid scraping service, AI service, or İETT account.

Each distinct subscribed line is polled once per cycle regardless of subscriber count, including when
subscribers mute alerts. Default checks target 60 seconds, with ±10% jitter and two concurrent
requests. Under heavy load or upstream throttling the interval can grow. Manual checks share
successful results captured within the last ten seconds and use the
same concurrency limit and backoff as automatic checks. They also persist captured notices and
enqueue automatic alerts without creating duplicate occurrences or deliveries. Failed refreshes
explicitly identify any displayed notices as the last successful result. Failed requests back off up
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

Set `HEALTHCHECK_PING_URL` (for example a [healthchecks.io](https://healthchecks.io) check with a
one-minute period and ten-minute grace) for an independent dead-man alert. The scheduler pings it every
minute **only while** workers are alive, source data is fresh, and no due delivery has been overdue for
five minutes. When pings stop, the service alerts you, including when the whole server is down. A
long İETT outage also silences it and may mean the source format changed. `/readyz` uses the same
rule. The URL is a secret and is never logged.

For monitoring without a new service account, the optional [GitHub Actions monitor](ops/README.md)
checks the VM from outside Oracle every five minutes and sends Telegram incident/recovery alerts.
It checks the same readiness endpoint, backup age and failed restore tests. It uses a separate SSH
key restricted to a root-owned status command; it cannot open a shell or read subscriber records.
GitHub scheduling can be delayed, so this is an operational alert target, not a delivery guarantee.

For the 59RH pilot, check `/status`, observe a real announcement if available, verify detection time
and the official link, and check that disappearance produces no false restoration alert. The automated
simulated-cancellation test covers capture → persistent queue → Telegram send even after source deletion.
The public source had no current 59RH notices during initial inspection; historical cancellations
from before deployment cannot be recovered through this feed.

### Backups and updates

The database holds every subscriber, so it is backed up nightly, encrypted on the server with
[restic](https://restic.net), to S3-compatible object storage outside the VM (for example OCI Object
Storage's S3 endpoint, `https://<namespace>.compat.objectstorage.<region>.oraclecloud.com`).
`ops/backup.sh` dumps the database to a temporary file, verifies the archive, uploads it, keeps 7 daily
and 4 weekly snapshots (about five weeks), and runs `restic check` on Sundays. A failed dump is never
uploaded. Each snapshot is a full dump, so storage is roughly the dump size times eleven. Use a bucket
dedicated to this repository: retention is applied to every `iett-db` snapshot in it.
The supplied cron file uses a UTC server clock: backups run daily at 03:17 Istanbul time and restore
drills on the first of each month at 04:41. A lock prevents overlapping backup and restore jobs.
Success/failure markers under `/var/lib/iett-monitor` let the external monitor detect backup failures
without a Healthchecks account. Install `ops/iett-backup.logrotate` in `/etc/logrotate.d/iett-backup`
to rotate the job log.

One-time setup on the server (the bot runs as `sudo` Docker, so cron runs as root):

1. Create a private, dedicated bucket and an S3 access key pair for it. Install restic: `sudo apt install restic`.
2. Create `.env.backup` next to `.env` and `chmod 600` it (Git-ignored). Root sources this file, so the
   scripts refuse any other mode. Quote values containing spaces:

   ```sh
   RESTIC_REPOSITORY=s3:https://<namespace>.compat.objectstorage.eu-frankfurt-1.oraclecloud.com/<bucket>
   RESTIC_PASSWORD=<long random password>
   AWS_ACCESS_KEY_ID=<access key>
   AWS_SECRET_ACCESS_KEY=<secret key>
   AWS_DEFAULT_REGION=eu-frankfurt-1
   BACKUP_PING_URL=<healthchecks.io check URL, optional>
   ```

   **Store `RESTIC_PASSWORD` in a password manager too: without it the backups cannot be read.**
3. Initialize the repository once: `sudo bash -c 'set -a; . ./.env.backup; set +a; restic init'`.
4. Create a [healthchecks.io](https://healthchecks.io) check with a one-day period and six-hour grace,
   and put its URL in `BACKUP_PING_URL`. It alerts you when a backup fails (`/fail`) or does not run. Failures before the settings load
   (a missing file or a wrong mode) cannot ping, so they show up as a missed backup.
5. Run `sudo ops/backup.sh` once, then `sudo ops/restore-drill.sh`, then install the schedule:
   `sudo cp ops/iett-backup.cron /etc/cron.d/iett-backup` (check the path and server time inside).

`ops/restore-drill.sh` restores the newest snapshot into a throwaway PostgreSQL container with no
network and compares row counts with the live database. Repeat it after every change to the backup
setup and at least quarterly. To recover from a lost server, install Docker and restic, recreate `.env`
and `.env.backup`, bring up only the database, restore the dump into the **new, empty** database, and
start the bot:

```sh
sudo bash -c 'set -a; . ./.env.backup; set +a; restic restore latest --tag iett-db --target /var/tmp/restore'
sudo docker compose -p iett-alerts -f compose.yaml -f compose.production.yaml up -d db
sudo docker compose -p iett-alerts -f compose.yaml -f compose.production.yaml exec -T db \
  pg_restore -U iett -d iett --no-owner < /var/tmp/restore/iett.dump
sudo docker compose -p iett-alerts -f compose.yaml -f compose.production.yaml up -d
sudo rm -rf /var/tmp/restore
```

Backups contain personal data: restrict access to the bucket and keys. Data removed with `/delete_me`
can remain in backups for up to about five weeks, and restoring a backup can bring it back, so
re-apply recent deletion requests after a restore. A quick manual dump is still possible with
`docker compose exec -T db pg_dump -U iett -d iett -Fc > backups/iett.dump`.

Before an update, run `sudo ops/backup.sh`. Pull the code and run `docker compose up -d --build`.
Migrations are forward-only and transactional. Roll back application code only if compatible with the
installed schema; otherwise restore the pre-update backup into a fresh database. Deleting a Docker
volume deletes the database; ordinary container rebuilds preserve it.

## Privacy, retention, and configuration

The bot operates in private chats. It stores Telegram chat/user ID, language, pause/block state,
selected lines, notification hours, departure picks, and delivery receipts. It does not require a phone number,
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
