#!/usr/bin/env bash
# Optional local test database, owned by this script. Never touches an existing cluster.
set -euo pipefail
TASK_PG_DIR="${COMMITCUT_PG_DIR:-/tmp/commitcut-postgres}"
TASK_PG_PORT="${COMMITCUT_PG_PORT:-55439}"
TASK_PG_BIN="${COMMITCUT_PG_BIN:-}"
if [[ -n "$TASK_PG_BIN" ]]; then export PATH="$TASK_PG_BIN:$PATH"; fi
case "${1:-start}" in
  start)
    mkdir -p "$TASK_PG_DIR"
    if [[ ! -f "$TASK_PG_DIR/data/PG_VERSION" ]]; then initdb -D "$TASK_PG_DIR/data" -A trust -U commitcut --no-locale > "$TASK_PG_DIR/init.log"; fi
    pg_ctl -D "$TASK_PG_DIR/data" -l "$TASK_PG_DIR/server.log" -o "-h 127.0.0.1 -p $TASK_PG_PORT -k $TASK_PG_DIR" start
    createdb -h 127.0.0.1 -p "$TASK_PG_PORT" -U commitcut commitcut_test
    echo "COMMITCUT_DATABASE_URL=postgresql://commitcut@127.0.0.1:$TASK_PG_PORT/commitcut_test"
    ;;
  stop) pg_ctl -D "$TASK_PG_DIR/data" stop -m fast ;;
  *) echo 'usage: local-postgres.sh [start|stop]' >&2; exit 2 ;;
esac
