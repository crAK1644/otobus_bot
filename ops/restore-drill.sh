#!/usr/bin/env bash
# Prove the latest backup restores: restore it into a throwaway PostgreSQL container (no network,
# memory-only data) and compare row counts with the live database. Same settings as backup.sh.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
. "$root/ops/load-env.sh"
need RESTIC_REPOSITORY
need RESTIC_PASSWORD

compose=(docker compose -p iett-alerts -f compose.yaml -f compose.production.yaml)
name="iett-restore-drill-$$"
umask 077
tmp="${TMPDIR:-/var/tmp}"
# Left by a killed run (same 12 h ceiling as backup.sh).
find "$tmp" -maxdepth 1 -name 'iett-drill.*' -mmin +720 -exec rm -rf {} +
work="$(mktemp -d "$tmp/iett-drill.XXXXXX")"
trap 'docker rm -f "$name" > /dev/null 2>&1 || true; rm -rf "$work"' EXIT

# Resolve the newest snapshot once and restore exactly that one.
snapshot="$(restic snapshots --tag iett-db --latest 1 --json | grep -o '"short_id":"[0-9a-f]*"' | head -1 | cut -d'"' -f4)"
[ -n "$snapshot" ]
echo "restoring snapshot $snapshot"
restic restore "$snapshot" --target "$work"
[ -s "$work/iett.dump" ]

# Keep the image in step with compose.yaml.
docker run -d --rm --name "$name" --network none --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=iett -e POSTGRES_DB=iett -e POSTGRES_PASSWORD=drill postgres:16-alpine > /dev/null
# The entrypoint's temporary init server also answers pg_isready, so wait for the real one.
ready=0
for _ in $(seq "${DRILL_WAIT_SECONDS:-60}"); do
  if [[ "$(docker logs "$name" 2>&1)" == *'init process complete'* ]] &&
    docker exec "$name" pg_isready -U iett -d iett -q; then
    ready=1
    break
  fi
  sleep 1
done
[ "$ready" = 1 ] || { echo "restore container did not become ready" >&2; docker logs "$name" 2>&1 | tail -5 >&2; exit 1; }
docker exec -i "$name" pg_restore -U iett -d iett --no-owner < "$work/iett.dump"

count() { "$@" psql -U iett -d iett -tA -c "SELECT count(*) FROM $table"; }
status=0
for table in users subscriptions notices; do
  restored="$(count docker exec "$name")"
  live="$(count "${compose[@]}" exec -T db)"
  echo "$table: restored=$restored live=$live"
  if [ "$restored" = 0 ] && [ "$live" != 0 ]; then status=1; fi
done
if [ "$status" = 0 ]; then echo "restore drill ok (counts may trail live by up to a day)"; fi
exit "$status"
