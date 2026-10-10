*Alice never sees Bob's laptop.*

# BUILD_LOG

> Copy this file to `BUILD_LOG.md`. Fill in each stage **as you go**, not at the end: the
> decision **before** you prompt your agent, the prediction **before** you run the check.
>
> This is your record of what *you* understood. A person reads it for `G-DESIGN` and
> `G-ENFORCE`. Your coding agent is instructed not to write it for you; if the words aren't
> yours, it shows.
>
> Three lines per part is plenty. "I expected X, got Y, because Z" beats a paragraph.

---

## Stage 1: the database
- **I decided:** to go with **Container/Volume reset**, though I took an A → B → A path to get here. Initially, I thought about Option A, then switched to Option B (resetting via SQL scripts) because it kept the container and connections alive and seemed faster. However, I ultimately reverted back to Option A. The reasoning is that Option B required modifying `seed.sql` with `DROP TABLE` commands and managing a separate `grants.sql` to reapply permissions. Option A (`docker compose down -v` followed by `start`) guarantees a 100% pristine database state on a fresh volume every time. While this means the Toolbox process (which will run via npx, not as a container) will need to be restarted after a reset in Stage 2, the absolute certainty of a clean state without complex script management is worth the tradeoff.

- **I predicted:** 
  1. Running `./run.sh reset` will tear down the database container, completely wipe the Docker volume, and start a fresh instance.
  2. Because it's a fresh volume, `db/seed.sql` (without needing `DROP TABLE`) will run cleanly once, and `db/roles.sh` will set up the `toolbox_user` and its permissions perfectly during boot.
  3. Querying the user count and customer orders will show a fresh state, with `order_id`s cleanly numbered from 1 to 17 (satisfying M-1, EVALS §2), as the database sequences start from scratch.
  4. The insert command into `actions_log` will succeed, proving the `toolbox_user` retains its restricted grants (it can read orders and write to logs, but cannot mutate orders—satisfying M-6/T-MUTATE).
- **What happened (paste output):** 
```
./run.sh reset
[+] up 2/3
 ✔ Volume customer-support-agent_pgdata   Created                           0.0s
 ✔ Network customer-support-agent_default Created                           
0.0s
[+] up 3/3
 ⠙ Container cs_db                        Starting                          
 ⠹ Container cs_db                        Starting                          
 ✔ Container cs_db                        Started                           0.2s
  waiting for postgres..
  ✔ postgres on 127.0.0.1:5433

./run.sh psql -c "select count(*) from users;"
 count 
-------
    10
(1 row)

./run.sh psql -c "select order_id, customer_email, status from customer_orders order by order_id;"
 order_id |     customer_email      |   status   
----------+-------------------------+------------
        1 | alice.jones@example.com | DELIVERED
        2 | alice.jones@example.com | DELIVERED
        3 | alice.jones@example.com | SHIPPED
        4 | alice.jones@example.com | PROCESSING
        5 | bob.smith@techmail.com  | DELIVERED
        6 | bob.smith@techmail.com  | CANCELLED
        7 | bob.smith@techmail.com  | PROCESSING
        8 | charlie.d@webmail.com   | DELIVERED
        9 | diana.prince@hero.net   | DELIVERED
       10 | diana.prince@hero.net   | RETURNED
       11 | evan.g@bizcorp.com      | SHIPPED
       12 | fiona.shrek@swamp.com   | CANCELLED
       13 | george.j@jungle.com     | PROCESSING
       14 | hannah.m@school.edu     | DELIVERED
       15 | ian.malcolm@chaos.com   | DELIVERED
       16 | julia.child@kitchen.com | DELIVERED
       17 | julia.child@kitchen.com | PROCESSING
(17 rows)

./run.sh psql -c "insert into actions_log (user_email, action_type, parameters) values ('x','RETURN_ITEM','{}');"
ERROR:  new row for relation "actions_log" violates check constraint "actions_log_action_type_check"
DETAIL:  Failing row contains (1, 2026-10-10 12:19:22.694927+00, x, RETURN_ITEM, {}).
```
- **Analysis of the result:** Prediction 4 was wrong. I expected the insert to succeed and prove the `toolbox_user`'s permissions, but it failed. The failure was strictly schema-related: `RETURN_ITEM` is not a valid value in the `actions_log_action_type_check` constraint. Furthermore, the premise of the test was flawed: `./run.sh psql` connects as the database administrator (`shop_admin`), not as the `toolbox_user`. Therefore, even if the payload was valid, this command wouldn't have actually tested the restricted user grants.

- **Log it question (What would break if the seed ran twice?):** If `seed.sql` somehow ran twice without the `DROP TABLE` line, it would crash immediately on the `CREATE TABLE users` statement with a "relation already exists" error. Even if it bypassed that and reached the data population, it wouldn't fail on primary keys (since `SERIAL` would just auto-increment past 17), but rather on the `UNIQUE` constraint for the user's `email`. However, the beauty of choosing **Option A** is that it structurally prevents this scenario altogether. The official Postgres Docker image only executes scripts in `/docker-entrypoint-initdb.d` when the data volume is completely empty. By wiping the volume on every reset, we don't just work around the issue—we guarantee the seed script only ever runs exactly once per lifecycle.

## Stage 2: the tools, and where access control lives
- **Before reading SPEC R-1, I thought ownership belonged in:** in the SQL
- **I decided:** to enforce data ownership directly in the SQL queries of the tools (`get-order-status` and `find-customer-orders`), but with a critical security constraint: the customer's email must be bound directly from the authenticated session context, not provided by the LLM. As highlighted by R-1, relying on the prompt to enforce "only show this user's data" is a vulnerability. Following M-4 and M-5, I will use `toolbox-core`'s bound parameters to bind the session's email (via a callable or context value) to a SQL parameter (e.g., `WHERE customer_email = $2`). This guarantees that no matter what the LLM attempts to do, the database will strictly filter the query to only return rows belonging to the logged-in user.
- **What happened (order 5 as alice):**

```
./run.sh status
  ✔ postgres on 127.0.0.1:5433
  ✔ toolbox on :5000

curl -s -X POST localhost:5000/api/tool/get-order-status/invoke \
  -H 'content-type: application/json' -d '{"order_id": 3, "customer_email": "alice.jones@example.com"}'
{"result":"[{\"order_id\":3,\"status\":\"SHIPPED\",\"items\":[{\"price\":120,\"product\":\"Mechanical Keyboard\",\"qty\":1}],\"order_date\":\"2026-10-08T09:59:46.532726-03:00\",\"total_amount\":120.00,\"delivery_address\":\"123 Market St, Springfield\"}]"}

curl -s -X POST localhost:5000/api/tool/get-order-status/invoke \
  -H 'content-type: application/json' -d '{"order_id": 5, "customer_email": "alice.jones@example.com"}'
{"result":"[]"}
```

The Alice's order 3 returned with success, and the order 5 (Bob's order) returned empty.

## Stage 3: the agent, and a bare CLI
- **I decided (tools loaded, how the email is bound):**
- **I predicted the "order 5 for bob" request would:**
- **What happened:**

## Stage 4: the pipeline and its events
- **My hand-sketched CLI lines:**
- **I predicted the first event after the agent stage would be:**
- **What happened:**

## Stage 5: one trace per turn
- **I decided (what goes in span attributes, who can see Phoenix):**
- **Trace id:**
- **One thing the trace showed that the reply didn't:**

## Stage 6: Sanitizer and Security Judge
- **I decided (who may block, which A2A method):**
- **Predicted vs actual X01 latency:**
- **When I stopped the Judge, my pipeline first:**
- **Failing trajectory saved at:**

## Stage 7: the Guardrail
- **Three messages that must pass / three that must not (written before the prompt):**
- **False blocks / off-topic blocks, per prompt version:**

  | Version | What I changed | Legit false blocks | Off-topic blocked |
  |---|---|---|---|
  | v1 | | | |

## Stage 8: Masker and memory
- **I decided (what counts as PII, the cutoff):**
- **My planted memory's score, and whether my cutoff kept it:**
- **What the Masker reported on my PII test:**

## Stage 9: the web UI
- **My sketch, in words:**
- **Something the UI shows that the CLI doesn't (feature or leak?):**

## Stage 10: the eval runner
- **How I handled the memory waits:**
- **First run's failing rows, and what I changed:**
- **Second run: see `reports/eval.json` (don't retype numbers here).**
- **Successful turn I read end to end (trace id), and what it taught me:**
- **Failing turn I read end to end (trace id), and what it taught me:**
