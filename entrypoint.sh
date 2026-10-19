#!/bin/sh
# Container entry point, compatible with legacy's CronJobs (ETL/entrypoint.sh):
# - CLOUDSQL_INSTANCE set: start the Cloud SQL proxy in the container and wait for it, as
#   legacy did (the existing jobs run as the cluster's cloud-sql-proxy identity).
# - ETL_COMMAND or ETL_CONNECTORS set and no explicit command: `etl legacy` maps the legacy
#   job settings to v2 runs. Otherwise the arguments are the `etl` command (local use, Cloud Run).
set -eu
log() { printf '[entrypoint] %s\n' "$*"; }

PROXY_PID=""
if [ -n "${CLOUDSQL_INSTANCE:-}" ]; then
  PORT="${CLOUDSQL_PORT:-5432}"
  log "Starting Cloud SQL proxy for ${CLOUDSQL_INSTANCE} on 127.0.0.1:${PORT}"
  cloud-sql-proxy --structured-logs --port="${PORT}" "${CLOUDSQL_INSTANCE}" &
  PROXY_PID=$!
  python - "${PORT}" <<'PY'
import socket, sys, time
port = int(sys.argv[1])
for _ in range(180):  # up to 3 minutes, as legacy
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            print("[entrypoint] Proxy is up")
            sys.exit(0)
    except OSError:
        time.sleep(1)
print("[entrypoint] Proxy never came up", file=sys.stderr)
sys.exit(1)
PY
fi

if [ -n "${ETL_COMMAND:-}${ETL_CONNECTORS:-}" ] && { [ "$#" -eq 0 ] || [ "$1" = "list" ]; }; then
  set -- legacy
fi

set +e
etl "$@"
RC=$?
set -e

if [ -n "${PROXY_PID}" ]; then
  kill -TERM "${PROXY_PID}" 2>/dev/null || true
  wait "${PROXY_PID}" 2>/dev/null || true
fi
log "etl exit code: ${RC}"
exit "${RC}"
