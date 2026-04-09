---
name: database-connection-runbook
description: Procedure for diagnosing database connection pool exhaustion and connection failures
version: 1.0.0
applies_to: ["database_*", "db_connection"]
---

# Database Connection Runbook

## 1. Confirm the Symptom

Distinguish between connection pool exhaustion, authentication failures, and network-level connectivity issues.

Check application logs for the specific error:
- `connection pool exhausted` or `too many clients` -- pool exhaustion
- `password authentication failed` -- credential issue
- `connection refused` or `timeout` -- network or server down

Query active connections on the PostgreSQL primary:
```sql
SELECT count(*), state, usename, application_name
FROM pg_stat_activity
GROUP BY state, usename, application_name
ORDER BY count DESC;
```

## 2. Connection Pool Exhaustion

This is the most common database alert, especially during end-of-day batch reconciliation when risk engines and reporting services spike concurrent queries.

### 2a. Check Current Pool State

Query pgbouncer stats (if pgbouncer is in the path):
```bash
psql -h <pgbouncer-host> -p 6432 -U pgbouncer pgbouncer -c "SHOW POOLS;"
psql -h <pgbouncer-host> -p 6432 -U pgbouncer pgbouncer -c "SHOW CLIENTS;"
psql -h <pgbouncer-host> -p 6432 -U pgbouncer pgbouncer -c "SHOW STATS;"
```

Check Datadog metrics:
```
postgresql.connections{db:<database>} by {state}
pgbouncer.pools.cl_active{host:<pgbouncer-host>}
pgbouncer.pools.cl_waiting{host:<pgbouncer-host>}
```

### 2b. Identify Connection Hogs

Find services holding the most connections:
```sql
SELECT application_name, client_addr, count(*)
FROM pg_stat_activity
WHERE state != 'idle'
GROUP BY application_name, client_addr
ORDER BY count DESC
LIMIT 20;
```

Common culprits in trading infrastructure:
- **EOD batch reconciliation** jobs that open many parallel connections
- **Risk calculation engines** running long analytical queries
- **Report generation** services creating connections per-report instead of using a pool

### 2c. Find Long-Running Queries

```sql
SELECT pid, now() - pg_stat_activity.query_start AS duration, query, state, application_name
FROM pg_stat_activity
WHERE (now() - pg_stat_activity.query_start) > interval '5 minutes'
  AND state != 'idle'
ORDER BY duration DESC;
```

If a risk calculation query is blocking, verify whether it is safe to cancel:
```sql
SELECT pg_cancel_backend(<pid>);  -- graceful
SELECT pg_terminate_backend(<pid>);  -- forceful, use only if cancel fails
```

## 3. PgBouncer Misconfiguration

Common misconfigurations after infrastructure changes:

1. **Pool mode mismatch**: Transaction pooling is required for most trading services. Session pooling causes connection hoarding.
   ```bash
   grep pool_mode /etc/pgbouncer/pgbouncer.ini
   ```
2. **max_client_conn too low**: Should be at least 2x the sum of all application pool sizes.
3. **server_idle_timeout too aggressive**: Causes excessive reconnection overhead during bursty EOD workloads.
4. **default_pool_size insufficient**: Each database/user pair gets this many server connections. Check it covers peak demand.

## 4. Replication Lag on Read Replicas

Read replicas serve real-time dashboards (P&L, position views, risk dashboards). Lag makes data stale.

Check replication lag:
```sql
-- On the replica:
SELECT now() - pg_last_xact_replay_timestamp() AS replication_lag;
```

Check Datadog:
```
postgresql.replication.delay{host:<replica-host>}
```

If lag exceeds 30 seconds:
1. Check if the replica is under heavy read load (dashboard queries, reporting).
2. Check if a large write transaction on the primary (bulk trade import, EOD settlement) is generating excessive WAL.
3. Verify `max_wal_senders` and `wal_keep_size` on the primary.

## 5. Immediate Remediation

| Symptom | Action |
|---------|--------|
| Pool exhaustion from batch jobs | Reduce batch parallelism or stagger job start times |
| Long-running query blocking pool | Cancel the query if safe (non-write, or idempotent) |
| PgBouncer misconfiguration | Update config and reload: `pgbouncer -R` |
| Replica lag > 60s | Redirect dashboard traffic to primary temporarily; investigate replica |

## 6. Market Hours Impact Assessment

1. Is the database serving **order management** or **risk calculation** workloads?
2. Are real-time position dashboards showing stale data due to replica lag?
3. Is the FIX gateway able to persist order state?

If any trading-path service cannot write to the database during market hours, this is **SEV-1**.

## 7. Escalation Path

| Severity | Condition | Action |
|----------|-----------|--------|
| SEV-1 | Trading-path writes blocked during market hours | Page DBA + on-call SRE + trading desk lead |
| SEV-2 | Read replica lag > 60s or dashboard data stale | Page DBA + on-call SRE |
| SEV-3 | Batch job connection issues outside market hours | Create ticket for DBA team |

## Compliance Note

Never terminate database connections serving regulatory reporting queries (identifiable by `application_name` containing `reg-reporting` or `compliance`). These queries may be required for real-time regulatory obligations. Consult compliance before cancelling.
