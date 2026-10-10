#!/usr/bin/env bash
# One entry point for every service, started in dependency order (TECHNICAL §5).
# Order (TECHNICAL §5): Postgres -> Toolbox -> Judge -> Masker -> Phoenix -> web on :8000.
# Background services write logs/<name>.log and logs/<name>.pid.
#
#   ./run.sh start    start every service, waiting for each to be healthy
#   ./run.sh stop     stop every service (keeps data)
#   ./run.sh status   show what is up
#   ./run.sh reset    throw the database volume away and reseed (EVALS §2)
#   ./run.sh psql     open psql as the admin user (extra args are passed to psql)
#   ./run.sh stop-judge   stop only the Security Judge (stage 6 fail-loud check)
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

# --- A2A guard services ------------------------------------------------------
# Each guard is its own process with its own port and agent card (J-1, K-1).
JUDGE_PORT=10002
MASKER_PORT=10003

a2a_healthy() {  # $1 = port
  curl -sf "http://127.0.0.1:$1/.well-known/agent-card.json" >/dev/null
}

a2a_start() {  # $1 = name, $2 = port, $3 = python module
  if a2a_healthy "$2"; then ok "$1 on :$2"; return 0; fi
  mkdir -p logs
  setsid nohup .venv/bin/python -m "$3" >"logs/$1.log" 2>&1 &
  echo $! >"logs/$1.pid"
  printf '  waiting for %s' "$1"
  for _ in $(seq 1 60); do
    if a2a_healthy "$2"; then echo; ok "$1 on :$2"; return 0; fi
    printf '.'; sleep 1
  done
  echo; fail "$1 did not start"; tail -20 "logs/$1.log"; exit 1
}

a2a_stop() {  # $1 = name
  if [[ -f "logs/$1.pid" ]]; then
    kill -- "-$(cat "logs/$1.pid")" 2>/dev/null || true
    rm -f "logs/$1.pid"
  fi
}

a2a_status() {  # $1 = name, $2 = port
  if a2a_healthy "$2"; then ok "$1 on :$2"; else fail "$1 down"; return 1; fi
}

# --- Phoenix ----------------------------------------------------------------
# Its own long-lived process with storage in .phoenix/, so traces outlive the agent (O-1,
# R-10). It runs from .venv-phoenix so its dependencies never fight ADK's OpenTelemetry pins.
PHOENIX_PORT=6006

phoenix_healthy() {
  curl -sf "http://127.0.0.1:${PHOENIX_PORT}/healthz" >/dev/null
}

phoenix_start() {
  if phoenix_healthy; then ok "phoenix on :${PHOENIX_PORT}"; return 0; fi
  if [[ ! -x .venv-phoenix/bin/phoenix ]]; then
    fail "phoenix not installed: python3 -m venv .venv-phoenix && .venv-phoenix/bin/pip install arize-phoenix"
    exit 1
  fi
  mkdir -p logs .phoenix
  PHOENIX_WORKING_DIR="$PWD/.phoenix" PHOENIX_HOST=127.0.0.1 PHOENIX_PORT="${PHOENIX_PORT}" \
    setsid nohup .venv-phoenix/bin/phoenix serve >logs/phoenix.log 2>&1 &
  echo $! >logs/phoenix.pid
  printf '  waiting for phoenix'
  for _ in $(seq 1 90); do
    if phoenix_healthy; then echo; ok "phoenix on :${PHOENIX_PORT}"; return 0; fi
    printf '.'; sleep 1
  done
  echo; fail "phoenix did not start"; tail -20 logs/phoenix.log; exit 1
}

phoenix_stop() {
  if [[ -f logs/phoenix.pid ]]; then
    kill -- "-$(cat logs/phoenix.pid)" 2>/dev/null || true
    rm -f logs/phoenix.pid
  fi
}

phoenix_status() {
  if phoenix_healthy; then ok "phoenix on :${PHOENIX_PORT}"; else fail "phoenix down"; return 1; fi
}

# --- web front end ------------------------------------------------------------
WEB_PORT=8000

web_healthy() {
  curl -sf "http://127.0.0.1:${WEB_PORT}/" >/dev/null
}

web_start() {
  if web_healthy; then ok "web on http://127.0.0.1:${WEB_PORT}"; return 0; fi
  mkdir -p logs
  setsid nohup .venv/bin/python -m support.web >logs/web.log 2>&1 &
  echo $! >logs/web.pid
  printf '  waiting for web'
  for _ in $(seq 1 60); do
    if web_healthy; then echo; ok "web on http://127.0.0.1:${WEB_PORT}"; return 0; fi
    printf '.'; sleep 1
  done
  echo; fail "web did not start"; tail -20 logs/web.log; exit 1
}

web_stop() {
  if [[ -f logs/web.pid ]]; then
    kill -- "-$(cat logs/web.pid)" 2>/dev/null || true
    rm -f logs/web.pid
  fi
}

web_status() {
  if web_healthy; then ok "web on http://127.0.0.1:${WEB_PORT}"; else fail "web down"; return 1; fi
}

# --- commands ---------------------------------------------------------------
case "${1:-}" in
  start)  db_start; toolbox_start; a2a_start judge "$JUDGE_PORT" guards.judge.server
          a2a_start masker "$MASKER_PORT" guards.masker.server; phoenix_start; web_start ;;
  # The CLI never stops Phoenix; only this explicit command does (O-1).
  stop)   web_stop; phoenix_stop; a2a_stop masker; a2a_stop judge; toolbox_stop; docker compose stop >/dev/null; ok "stopped" ;;
  status) rc=0; db_status || rc=1; toolbox_status || rc=1; a2a_status judge "$JUDGE_PORT" || rc=1
          a2a_status masker "$MASKER_PORT" || rc=1; phoenix_status || rc=1; web_status || rc=1; exit $rc ;;
  # The Toolbox's connections die with the old database, so it restarts with it.
  reset)  toolbox_stop; db_reset; toolbox_start ;;
  psql)   shift; psql_admin "$@" ;;
  stop-judge) a2a_stop judge; ok "judge stopped" ;;
  *)      sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
