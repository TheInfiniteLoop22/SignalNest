#!/usr/bin/env bash
# Nightly logical backup of the SignalNest database.
#
#   crontab -e  (as the deploy user):
#   15 3 * * * cd /opt/signalnest/app && ENV_FILE=/opt/signalnest/.env.prod ./ops/backup.sh >> /var/log/signalnest-backup.log 2>&1
#
# Produces <BACKUP_DIR>/signalnest-<UTC timestamp>.dump (pg_dump custom format, already
# compressed) plus a .sha256 file, prunes old files, and runs BACKUP_UPLOAD_CMD if set.
# A backup that has never been restored is a hope, not a backup: see ops/restore-drill.sh.
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env.prod}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

BACKUP_DIR="${BACKUP_DIR:-/var/backups/signalnest}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/signalnest-$STAMP.dump"
TMP="$OUT.partial"

mkdir -p "$BACKUP_DIR"
umask 077

compose() { docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

started=$(date +%s)
# Dump inside the db container; stream to the host. -T: no TTY (cron has none).
compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner > "$TMP"

# Refuse to keep an empty or truncated file: pg_restore --list parses the whole TOC.
if [ ! -s "$TMP" ] || ! compose exec -T db pg_restore --list < "$TMP" > /dev/null; then
  echo "backup FAILED verification: $TMP" >&2
  rm -f "$TMP"
  exit 1
fi

mv "$TMP" "$OUT"
( cd "$BACKUP_DIR" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256" )
echo "backup ok: $OUT ($(wc -c < "$OUT") bytes, $(( $(date +%s) - started )) s)"

if [ -n "${BACKUP_UPLOAD_CMD:-}" ]; then
  # shellcheck disable=SC2086
  bash -c "$BACKUP_UPLOAD_CMD" _ "$OUT"
  echo "uploaded: $OUT"
fi

find "$BACKUP_DIR" -name 'signalnest-*.dump*' -type f -mtime +"$KEEP_DAYS" -print -delete
