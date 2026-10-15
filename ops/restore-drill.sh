#!/usr/bin/env bash
# Restore drill: prove the newest backup actually restores, into a throwaway container.
# Never touches the production database or its volume.
#
#   ./ops/restore-drill.sh [path/to/signalnest-*.dump]     (default: newest in $BACKUP_DIR)
#
# Prints timings so the recovery time is a measured number, not a guess. Exit code is non-zero
# if the restore fails or the sanity checks do not pass.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/signalnest}"
DUMP="${1:-$(ls -1t "$BACKUP_DIR"/signalnest-*.dump 2>/dev/null | head -n1)}"
[ -n "$DUMP" ] && [ -f "$DUMP" ] || { echo "no dump found" >&2; exit 2; }

NAME="signalnest-restore-drill-$$"
PGUSER_DRILL="drill"
PGDB_DRILL="drill"
cleanup() { docker rm -f "$NAME" > /dev/null 2>&1 || true; }
trap cleanup EXIT

t0=$(date +%s)
echo "dump: $DUMP ($(wc -c < "$DUMP") bytes)"
if [ -f "$DUMP.sha256" ]; then
  ( cd "$(dirname "$DUMP")" && sha256sum -c "$(basename "$DUMP").sha256" )
fi

docker run -d --name "$NAME" -e POSTGRES_USER="$PGUSER_DRILL" -e POSTGRES_PASSWORD=drill \
  -e POSTGRES_DB="$PGDB_DRILL" postgres:16 > /dev/null
for _ in $(seq 1 60); do
  docker exec "$NAME" pg_isready -U "$PGUSER_DRILL" -d "$PGDB_DRILL" > /dev/null 2>&1 && break
  sleep 1
done
t_ready=$(date +%s)

# --exit-on-error: a partial restore must not look like a success.
docker exec -i "$NAME" pg_restore -U "$PGUSER_DRILL" -d "$PGDB_DRILL" --no-owner --exit-on-error < "$DUMP"
t_restored=$(date +%s)

q() { docker exec "$NAME" psql -U "$PGUSER_DRILL" -d "$PGDB_DRILL" -tAc "$1"; }

fail=0
check() { # name, actual, expected-test
  if eval "$3"; then echo "ok   $1: $2"; else echo "FAIL $1: $2"; fail=1; fi
}

head_rev="$(q 'SELECT version_num FROM alembic_version')"
check "alembic revision present" "$head_rev" '[ -n "$2" ]'
users="$(q 'SELECT count(*) FROM users')"
monitors="$(q 'SELECT count(*) FROM monitors')"
runs="$(q 'SELECT count(*) FROM check_runs')"
echo "rows: users=$users monitors=$monitors check_runs=$runs"
dupes="$(q 'SELECT count(*) FROM (SELECT 1 FROM check_runs GROUP BY monitor_id, scheduled_for HAVING count(*) > 1) d')"
check "no duplicate run slots" "$dupes" '[ "$2" = "0" ]'
open_inc="$(q "SELECT count(*) FROM (SELECT 1 FROM incidents WHERE status='OPEN' GROUP BY monitor_id HAVING count(*) > 1) d")"
check "at most one open incident per monitor" "$open_inc" '[ "$2" = "0" ]'
orphans="$(q 'SELECT count(*) FROM check_runs r LEFT JOIN monitors m ON m.id = r.monitor_id WHERE m.id IS NULL')"
check "no orphaned runs" "$orphans" '[ "$2" = "0" ]'
idx="$(q "SELECT count(*) FROM pg_indexes WHERE indexname IN ('one_open_incident','ix_state_due')")"
check "key indexes restored" "$idx" '[ "$2" = "2" ]'

t_end=$(date +%s)
echo "timings: container ready $((t_ready - t0)) s, restore $((t_restored - t_ready)) s, checks $((t_end - t_restored)) s, total $((t_end - t0)) s"
[ "$fail" = "0" ] && echo "RESTORE DRILL PASSED" || { echo "RESTORE DRILL FAILED"; exit 1; }
