#!/usr/bin/env bash
# One entry point for every service, started in dependency order (TECHNICAL §5).
# Stage 1: only the database. Later stages add Toolbox, Judge, Masker, Phoenix, web.
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

# --- commands ---------------------------------------------------------------
case "${1:-}" in
  start)  db_start ;;
  stop)   docker compose stop >/dev/null; ok "stopped" ;;
  status) db_status ;;
  reset)  db_reset ;;
  psql)   shift; psql_admin "$@" ;;
  *)      sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
