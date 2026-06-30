#!/bin/bash
# Fix Xiaoya Docker video playback issue.
# Root cause: alist image (v0.54.87) hardcodes http:// for opentoken_auth_url in
# the driver config stored in SQLite, causing AliYun Open token refresh to fail.
#
# This script tries two strategies in order:
#   1. Patch the existing installation's SQLite DB (non-destructive)
#   2. Full reinstall using the official monlor/docker-xiaoya install.sh (if -r flag is passed)
#
# Usage:
#   bash fix_xiaoya_video.sh               # patch only
#   bash fix_xiaoya_video.sh -r            # patch, then reinstall if patch fails
#   bash fix_xiaoya_video.sh --reinstall   # skip patch, go straight to reinstall
#
# Requirements: docker, docker compose (v2)

set -eu

INSTALL_PATH="${XIAOYA_PATH:-/opt/xiaoya}"
CORRECT_URL="https://api.oplist.org/oauth2/token"
FORCE_REINSTALL=0
TRY_REINSTALL=0

for arg in "$@"; do
  case "$arg" in
    --reinstall) FORCE_REINSTALL=1 ;;
    -r)          TRY_REINSTALL=1 ;;
  esac
done

log()  { echo "[INFO]  $*"; }
warn() { echo "[WARN]  $*"; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

# ─── Strategy 1: patch SQLite inside running alist container ──────────────────

patch_db() {
  log "Attempting DB patch on existing installation at ${INSTALL_PATH} ..."

  if [[ ! -f "${INSTALL_PATH}/docker-compose.yml" ]]; then
    warn "docker-compose.yml not found at ${INSTALL_PATH}. Skipping patch."
    return 1
  fi

  # Identify the alist container name
  CONTAINER=$(docker compose -f "${INSTALL_PATH}/docker-compose.yml" \
    --env-file "${INSTALL_PATH}/env" ps -q alist 2>/dev/null | head -1)

  if [[ -z "$CONTAINER" ]]; then
    warn "alist container is not running. Starting services first ..."
    docker compose -f "${INSTALL_PATH}/docker-compose.yml" \
      --env-file "${INSTALL_PATH}/env" up -d alist
    sleep 5
    CONTAINER=$(docker compose -f "${INSTALL_PATH}/docker-compose.yml" \
      --env-file "${INSTALL_PATH}/env" ps -q alist 2>/dev/null | head -1)
  fi

  [[ -z "$CONTAINER" ]] && { warn "Could not find alist container."; return 1; }

  log "alist container: ${CONTAINER}"

  # Check if sqlite3 is available inside the container
  if ! docker exec "$CONTAINER" which sqlite3 >/dev/null 2>&1; then
    warn "sqlite3 not found inside the container."
    # Try installing it (Alpine/Debian)
    docker exec "$CONTAINER" sh -c "apk add --no-cache sqlite 2>/dev/null || apt-get install -y sqlite3 2>/dev/null" || true
  fi

  if ! docker exec "$CONTAINER" which sqlite3 >/dev/null 2>&1; then
    warn "Cannot install sqlite3 in container. Falling back to volume mount approach."
    patch_db_via_host || return 1
    return 0
  fi

  # Find data.db inside the container
  DB_PATH=$(docker exec "$CONTAINER" find /opt/alist/data /data -name "data.db" 2>/dev/null | head -1)
  [[ -z "$DB_PATH" ]] && { warn "data.db not found in container."; return 1; }
  log "Found database at container path: ${DB_PATH}"

  # Show current opentoken_auth_url values
  log "Current opentoken_auth_url values in DB:"
  docker exec "$CONTAINER" sqlite3 "$DB_PATH" \
    "SELECT id, driver, json_extract(addition, '$.opentoken_auth_url') FROM x_storages WHERE addition LIKE '%opentoken_auth_url%';" 2>/dev/null || true

  # Patch http:// -> https:// in opentoken_auth_url inside the addition JSON
  docker exec "$CONTAINER" sqlite3 "$DB_PATH" \
    "UPDATE x_storages
     SET addition = json_set(addition, '$.opentoken_auth_url', '${CORRECT_URL}')
     WHERE addition LIKE '%opentoken_auth_url%'
       AND (json_extract(addition, '$.opentoken_auth_url') LIKE 'http://%'
         OR json_extract(addition, '$.opentoken_auth_url') = '');"

  ROWS=$(docker exec "$CONTAINER" sqlite3 "$DB_PATH" \
    "SELECT changes();" 2>/dev/null || echo "0")
  log "Rows updated: ${ROWS}"

  if [[ "$ROWS" == "0" ]]; then
    warn "No rows matched the patch condition."
    # Check if the URL is already correct
    CURRENT=$(docker exec "$CONTAINER" sqlite3 "$DB_PATH" \
      "SELECT json_extract(addition, '$.opentoken_auth_url') FROM x_storages WHERE addition LIKE '%opentoken_auth_url%' LIMIT 1;" 2>/dev/null || echo "")
    if [[ "$CURRENT" == "$CORRECT_URL" ]]; then
      log "opentoken_auth_url is already set correctly: ${CURRENT}"
      return 0
    fi
    return 1
  fi

  log "Patch applied. Restarting alist to pick up changes ..."
  docker compose -f "${INSTALL_PATH}/docker-compose.yml" \
    --env-file "${INSTALL_PATH}/env" restart alist

  log "DB patch successful. Video playback should now work."
  return 0
}

# Fallback: patch by copying DB to host, modifying, copying back
patch_db_via_host() {
  log "Trying host-side patch (copy DB out, modify, copy back) ..."
  command -v sqlite3 >/dev/null 2>&1 || die "sqlite3 not found on host. Install it with: apt-get install sqlite3"

  CONTAINER=$(docker compose -f "${INSTALL_PATH}/docker-compose.yml" \
    --env-file "${INSTALL_PATH}/env" ps -q alist 2>/dev/null | head -1)
  [[ -z "$CONTAINER" ]] && return 1

  DB_PATH=$(docker exec "$CONTAINER" find /opt/alist/data /data -name "data.db" 2>/dev/null | head -1)
  [[ -z "$DB_PATH" ]] && return 1

  TMP_DB=$(mktemp /tmp/xiaoya_data.XXXXXX.db)
  docker cp "${CONTAINER}:${DB_PATH}" "$TMP_DB"

  sqlite3 "$TMP_DB" \
    "UPDATE x_storages
     SET addition = json_set(addition, '$.opentoken_auth_url', '${CORRECT_URL}')
     WHERE addition LIKE '%opentoken_auth_url%'
       AND (json_extract(addition, '$.opentoken_auth_url') LIKE 'http://%'
         OR json_extract(addition, '$.opentoken_auth_url') = '');"

  docker cp "$TMP_DB" "${CONTAINER}:${DB_PATH}"
  rm -f "$TMP_DB"

  docker compose -f "${INSTALL_PATH}/docker-compose.yml" \
    --env-file "${INSTALL_PATH}/env" restart alist
  log "Host-side patch applied and alist restarted."
}

# ─── Strategy 2: full reinstall using official install.sh ────────────────────

backup_credentials() {
  if [[ -f "${INSTALL_PATH}/env" ]]; then
    BACKUP_FILE="/tmp/xiaoya_env_backup_$(date +%Y%m%d_%H%M%S).env"
    cp "${INSTALL_PATH}/env" "$BACKUP_FILE"
    log "Credentials backed up to: ${BACKUP_FILE}"
    echo "$BACKUP_FILE"
  else
    echo ""
  fi
}

fresh_install() {
  log "Starting full reinstall ..."

  BACKUP=$(backup_credentials)

  log "Stopping and removing existing containers ..."
  if [[ -f "${INSTALL_PATH}/docker-compose.yml" ]]; then
    docker compose -f "${INSTALL_PATH}/docker-compose.yml" \
      --env-file "${INSTALL_PATH}/env" down --remove-orphans 2>/dev/null || true
  fi

  log "Removing Docker volumes ..."
  docker volume rm xiaoya media config cache meta 2>/dev/null || true

  log "Removing installation directory: ${INSTALL_PATH} ..."
  rm -rf "${INSTALL_PATH}"

  log "Running official install.sh ..."
  INSTALL_CMD="bash <(curl -sSL https://raw.githubusercontent.com/monlor/docker-xiaoya/main/install.sh)"

  if [[ -n "$BACKUP" ]]; then
    log "Your previous credentials are saved at: ${BACKUP}"
    log "You will need to re-enter your Aliyun tokens during setup."
    log ""
    log "To retrieve your previous tokens, run:"
    log "  cat ${BACKUP}"
    log ""
  fi

  log "Launching installer (interactive) ..."
  eval "$INSTALL_CMD"
}

# ─── Main ─────────────────────────────────────────────────────────────────────

main() {
  if [[ "$FORCE_REINSTALL" -eq 1 ]]; then
    fresh_install
    exit 0
  fi

  if patch_db; then
    log ""
    log "Fix complete. If videos still don't play:"
    log "  1. Clear browser cache and retry"
    log "  2. Run this script with --reinstall for a fresh deployment"
    exit 0
  fi

  if [[ "$TRY_REINSTALL" -eq 1 ]]; then
    warn "Patch failed. Proceeding with full reinstall as requested (-r flag) ..."
    fresh_install
  else
    warn ""
    warn "Patch could not be applied automatically."
    warn "To do a full reinstall, run:"
    warn "  bash fix_xiaoya_video.sh --reinstall"
    exit 1
  fi
}

main "$@"
