#!/usr/bin/env bash
# Nightly encrypted off-server backup of the production database with restic.
# Run as root from cron (see ops/iett-backup.cron). Settings live in .env.backup (mode 600, never
# committed): RESTIC_REPOSITORY, RESTIC_PASSWORD, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
# AWS_DEFAULT_REGION and optionally BACKUP_PING_URL (a healthchecks.io check). Once the settings
# are loaded, any failure pings /fail. Failures before that (missing or unsafe settings file, VM
# down) cannot, so the missing success ping is the alert for them.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
. "$root/ops/load-env.sh"

compose=(docker compose -p iett-alerts -f compose.yaml -f compose.production.yaml)
ping() { [ -z "${BACKUP_PING_URL:-}" ] || curl -fsS -m 10 --retry 3 -o /dev/null "$BACKUP_PING_URL$1" || true; }
trap 'ping /fail' ERR
need RESTIC_REPOSITORY
need RESTIC_PASSWORD

umask 077
tmp="${TMPDIR:-/var/tmp}"
# Plaintext left by a killed run. A run takes minutes and cron fires daily, so 12 h never hits a live one.
find "$tmp" -maxdepth 1 -name 'iett-dump.*' -mmin +720 -delete
dump="$(mktemp "$tmp/iett-dump.XXXXXX")"
trap 'rm -f "$dump"' EXIT

# Dump to a file first: a failed pg_dump must never become the newest snapshot.
"${compose[@]}" exec -T db pg_dump -U iett -d iett -Fc > "$dump"
[ -s "$dump" ]
"${compose[@]}" exec -T db pg_restore --list < "$dump" > /dev/null

restic backup --stdin --stdin-filename iett.dump --tag iett-db < "$dump"
# Dedicated repository; group by tag so snapshots from a replaced host are still pruned.
restic forget --tag iett-db --group-by tags --keep-daily 7 --keep-weekly 4 --prune
if [ "$(date +%u)" = 7 ]; then restic check; fi

ping ""
echo "$(date -u +%FT%TZ) backup ok ($(wc -c < "$dump") bytes)"
