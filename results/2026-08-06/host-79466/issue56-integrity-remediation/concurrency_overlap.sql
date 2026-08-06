WITH
events AS MATERIALIZED (
  SELECT ts,
    CASE WHEN category = 'k3.storage' AND name = 'read_operation' THEN 1 ELSE 0 END AS operation_delta,
    CASE WHEN category = 'k3.storage' AND name = 'read_request' THEN 1 ELSE 0 END AS request_delta,
    CASE WHEN category = 'k3.transfer' AND name = 'h2d' THEN 1 ELSE 0 END AS h2d_delta
  FROM slice
  WHERE dur > 0 AND ((category = 'k3.storage' AND name IN ('read_operation', 'read_request'))
    OR (category = 'k3.transfer' AND name = 'h2d'))
  UNION ALL
  SELECT ts + dur,
    CASE WHEN category = 'k3.storage' AND name = 'read_operation' THEN -1 ELSE 0 END,
    CASE WHEN category = 'k3.storage' AND name = 'read_request' THEN -1 ELSE 0 END,
    CASE WHEN category = 'k3.transfer' AND name = 'h2d' THEN -1 ELSE 0 END
  FROM slice
  WHERE dur > 0 AND ((category = 'k3.storage' AND name IN ('read_operation', 'read_request'))
    OR (category = 'k3.transfer' AND name = 'h2d'))
),
points AS MATERIALIZED (
  SELECT ts, SUM(operation_delta) AS operation_delta, SUM(request_delta) AS request_delta,
    SUM(h2d_delta) AS h2d_delta
  FROM events GROUP BY ts
),
sweep AS MATERIALIZED (
  SELECT ts,
    LEAD(ts) OVER (ORDER BY ts) AS next_ts,
    SUM(operation_delta) OVER window AS operations,
    SUM(request_delta) OVER window AS requests,
    SUM(h2d_delta) OVER window AS h2d
  FROM points
  WINDOW window AS (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
)
SELECT
  MAX(operations) AS peak_active_operations,
  MAX(requests) AS peak_active_requests,
  MAX(h2d) AS peak_active_h2d,
  SUM(CASE WHEN operations > 0 THEN next_ts - ts ELSE 0 END) AS operation_busy_ns,
  SUM(CASE WHEN operations > 1 THEN next_ts - ts ELSE 0 END) AS operation_parallel_ns,
  SUM(CASE WHEN requests > 0 THEN next_ts - ts ELSE 0 END) AS request_busy_ns,
  SUM(CASE WHEN requests > 1 THEN next_ts - ts ELSE 0 END) AS request_backlog_ns,
  SUM(CASE WHEN h2d > 0 THEN next_ts - ts ELSE 0 END) AS h2d_busy_ns,
  SUM(CASE WHEN operations > 0 AND h2d > 0 THEN next_ts - ts ELSE 0 END) AS operation_h2d_overlap_ns,
  SUM(CASE WHEN requests > 0 AND h2d > 0 THEN next_ts - ts ELSE 0 END) AS request_h2d_overlap_ns,
  SUM(CASE WHEN operations > 0 THEN operations * (next_ts - ts) ELSE 0 END) AS operation_concurrency_ns,
  SUM(CASE WHEN requests > 0 THEN requests * (next_ts - ts) ELSE 0 END) AS request_concurrency_ns
FROM sweep
WHERE next_ts IS NOT NULL;
