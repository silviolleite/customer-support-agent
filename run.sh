#!/usr/bin/env bash
# One entry point for every service, started in dependency order (TECHNICAL §5).
# Stage 2: database + MCP Toolbox. Later stages add Judge, Masker, Phoenix, web.
# Background services write logs/<name>.log and logs/<name>.pid.
#
#   ./run.sh start    start every service, waiting for each to be healthy
#   ./run.sh stop     stop every service (keeps data)
#   ./run.sh status   show what is up
#   ./run.sh reset    throw the database volume away and reseed (EVALS §2)
#   ./run.sh psql     open psql as the admin user (extra args are passed to psql)
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "missing .env (copy .env.example and fill it in)" >&2
  exit 1
fi
set -a; source .env; set +a

ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✘\033[0m %s\n' "$*"; }

# --- database ---------------------------------------------------------------
db_healthy() {
  [[ "$(docker inspect -f '{{.State.Health.Status}}' cs_db 2>/dev/null)" == "healthy" ]]
}

db_start() {
  docker compose up -d db >/dev/null
  printf '  waiting for postgres'
  for _ in $(seq 1 60); do
    if db_healthy; then echo; ok "postgres on 127.0.0.1:${POSTGRES_PORT}"; return 0; fi
    printf '.'; sleep 1
  done
  echo; fail "postgres did not become healthy"; docker compose logs --tail 30 db; exit 1
}

db_status() {
  if db_healthy; then ok "postgres on 127.0.0.1:${POSTGRES_PORT}"; else fail "postgres down"; return 1; fi
}

# Throw the volume away and start again: the init scripts reseed from scratch.
# Anything that holds a connection (the Toolbox, from stage 2) must be restarted after.
db_reset() {
  docker compose down -v >/dev/null 2>&1 || true
  db_start
}

psql_admin() {
  docker exec -i cs_db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"
}

# --- MCP Toolbox -------------------------------------------------------------
TOOLBOX_VERSION=1.13.1
TOOLBOX_PORT=5000

toolbox_healthy() {
  curl -sf "http://127.0.0.1:${TOOLBOX_PORT}/api/toolset/support_agent" >/dev/null
}

toolbox_start() {
  if toolbox_healthy; then ok "toolbox on :${TOOLBOX_PORT}"; return 0; fi
  mkdir -p logs
  setsid nohup npx -y "@toolbox-sdk/server@${TOOLBOX_VERSION}" --config mcp_toolbox/tools.yaml \
    --enable-api --address 127.0.0.1 --port "${TOOLBOX_PORT}" --disable-version-check \
    --allowed-hosts "127.0.0.1:${TOOLBOX_PORT},localhost:${TOOLBOX_PORT}" \
    --allowed-origins "http://127.0.0.1:8000,http://localhost:8000" \
    >logs/toolbox.log 2>&1 &
  echo $! >logs/toolbox.pid
  printf '  waiting for toolbox'
  for _ in $(seq 1 60); do
    if toolbox_healthy; then echo; ok "toolbox on :${TOOLBOX_PORT}"; return 0; fi
    printf '.'; sleep 1
  done
  echo; fail "toolbox did not start"; tail -20 logs/toolbox.log; exit 1
}

toolbox_stop() {
  if [[ -f logs/toolbox.pid ]]; then
    # setsid made npx a group leader; killing the group also stops the server it spawned.
    kill -- "-$(cat logs/toolbox.pid)" 2>/dev/null || true
    rm -f logs/toolbox.pid
  fi
}

toolbox_status() {
  if toolbox_healthy; then ok "toolbox on :${TOOLBOX_PORT}"; else fail "toolbox down"; return 1; fi
}

# --- commands ---------------------------------------------------------------
case "${1:-}" in
  start)  db_start; toolbox_start ;;
  stop)   toolbox_stop; docker compose stop >/dev/null; ok "stopped" ;;
  status) rc=0; db_status || rc=1; toolbox_status || rc=1; exit $rc ;;
  # The Toolbox's connections die with the old database, so it restarts with it.
  reset)  toolbox_stop; db_reset; toolbox_start ;;
  psql)   shift; psql_admin "$@" ;;
  *)      sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
