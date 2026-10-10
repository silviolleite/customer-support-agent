#!/usr/bin/env bash
# Least-privilege login for the MCP Toolbox: it can read users and orders and append to
# actions_log, and nothing else. No UPDATE/DELETE on customer_orders (backs up M-6 / T-MUTATE).
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v tb_user="$TOOLBOX_DB_USER" -v tb_pass="$TOOLBOX_DB_PASSWORD" <<'SQL'
CREATE ROLE :"tb_user" LOGIN PASSWORD :'tb_pass';
GRANT CONNECT ON DATABASE :"DBNAME" TO :"tb_user";
GRANT USAGE ON SCHEMA public TO :"tb_user";
GRANT SELECT ON users, customer_orders, actions_log TO :"tb_user";
GRANT INSERT ON actions_log TO :"tb_user";
GRANT USAGE ON SEQUENCE actions_log_id_seq TO :"tb_user";
SQL
