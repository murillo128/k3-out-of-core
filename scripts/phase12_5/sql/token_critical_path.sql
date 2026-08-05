WITH
tokens AS MATERIALIZED (
  SELECT id AS token_slice_id, ts AS start_ts, ts + dur AS end_ts, dur AS wall_ns,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.request_id') AS INT) AS request_id,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.token_index') AS INT) AS token_index,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.n_tokens') AS INT) AS n_tokens
  FROM slice
  WHERE category = 'k3.request' AND name = 'process_ubatch' AND dur > 0
),
intervals AS MATERIALIZED (
  SELECT t.token_slice_id, MAX(t.start_ts, s.ts) AS start_ts, MIN(t.end_ts, s.ts + s.dur) AS end_ts,
    CASE
      WHEN s.category = 'k3.storage' THEN 1
      WHEN s.category = 'k3.transfer' THEN 2
      WHEN s.category = 'k3.cuda' AND s.name = 'synchronization' THEN 3
      WHEN s.category = 'k3.cuda' AND s.name IN ('memcpy', 'memset') THEN 4
      WHEN s.category = 'k3.cuda' AND s.name IN ('kernel', 'kernel_queued') THEN 5
      WHEN s.category = 'k3.scheduler' THEN 6
      WHEN s.category IN ('k3.cache.hot', 'k3.cache.cold') THEN 7
      WHEN s.category = 'k3.provider' THEN 8
      ELSE 9
    END AS priority
  FROM tokens t JOIN slice s ON s.dur > 0 AND s.ts < t.end_ts AND s.ts + s.dur > t.start_ts
  WHERE s.category IN ('k3.storage', 'k3.transfer', 'k3.cuda', 'k3.scheduler',
    'k3.cache.hot', 'k3.cache.cold', 'k3.provider')
),
events AS MATERIALIZED (
  SELECT token_slice_id, start_ts AS ts,
    CASE WHEN priority = 1 THEN 1 ELSE 0 END AS d1, CASE WHEN priority = 2 THEN 1 ELSE 0 END AS d2,
    CASE WHEN priority = 3 THEN 1 ELSE 0 END AS d3, CASE WHEN priority = 4 THEN 1 ELSE 0 END AS d4,
    CASE WHEN priority = 5 THEN 1 ELSE 0 END AS d5, CASE WHEN priority = 6 THEN 1 ELSE 0 END AS d6,
    CASE WHEN priority = 7 THEN 1 ELSE 0 END AS d7, CASE WHEN priority = 8 THEN 1 ELSE 0 END AS d8
  FROM intervals
  UNION ALL
  SELECT token_slice_id, end_ts,
    CASE WHEN priority = 1 THEN -1 ELSE 0 END, CASE WHEN priority = 2 THEN -1 ELSE 0 END,
    CASE WHEN priority = 3 THEN -1 ELSE 0 END, CASE WHEN priority = 4 THEN -1 ELSE 0 END,
    CASE WHEN priority = 5 THEN -1 ELSE 0 END, CASE WHEN priority = 6 THEN -1 ELSE 0 END,
    CASE WHEN priority = 7 THEN -1 ELSE 0 END, CASE WHEN priority = 8 THEN -1 ELSE 0 END
  FROM intervals
  UNION ALL SELECT token_slice_id, start_ts, 0, 0, 0, 0, 0, 0, 0, 0 FROM tokens
  UNION ALL SELECT token_slice_id, end_ts, 0, 0, 0, 0, 0, 0, 0, 0 FROM tokens
),
event_points AS MATERIALIZED (
  SELECT token_slice_id, ts, SUM(d1) AS d1, SUM(d2) AS d2, SUM(d3) AS d3, SUM(d4) AS d4,
    SUM(d5) AS d5, SUM(d6) AS d6, SUM(d7) AS d7, SUM(d8) AS d8
  FROM events GROUP BY token_slice_id, ts
),
sweep AS MATERIALIZED (
  SELECT token_slice_id, ts,
    SUM(d1) OVER window AS a1, SUM(d2) OVER window AS a2, SUM(d3) OVER window AS a3,
    SUM(d4) OVER window AS a4, SUM(d5) OVER window AS a5, SUM(d6) OVER window AS a6,
    SUM(d7) OVER window AS a7, SUM(d8) OVER window AS a8
  FROM event_points
  WINDOW window AS (PARTITION BY token_slice_id ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
),
classified AS MATERIALIZED (
  SELECT token_slice_id, ts AS start_ts, LEAD(ts) OVER (PARTITION BY token_slice_id ORDER BY ts) AS end_ts,
    CASE WHEN a1 > 0 THEN 1 WHEN a2 > 0 THEN 2 WHEN a3 > 0 THEN 3 WHEN a4 > 0 THEN 4
      WHEN a5 > 0 THEN 5 WHEN a6 > 0 THEN 6 WHEN a7 > 0 THEN 7 WHEN a8 > 0 THEN 8 ELSE 9 END AS priority
  FROM sweep
)
SELECT t.request_id, t.token_index, t.n_tokens, t.start_ts, t.end_ts, t.wall_ns,
  COALESCE(SUM(CASE WHEN c.priority = 1 THEN c.end_ts - c.start_ts ELSE 0 END), 0) AS storage_ns,
  COALESCE(SUM(CASE WHEN c.priority = 2 THEN c.end_ts - c.start_ts ELSE 0 END), 0) AS transfer_ns,
  COALESCE(SUM(CASE WHEN c.priority = 3 THEN c.end_ts - c.start_ts ELSE 0 END), 0) AS cuda_sync_ns,
  COALESCE(SUM(CASE WHEN c.priority = 4 THEN c.end_ts - c.start_ts ELSE 0 END), 0) AS cuda_memcpy_ns,
  COALESCE(SUM(CASE WHEN c.priority = 5 THEN c.end_ts - c.start_ts ELSE 0 END), 0) AS cuda_kernel_ns,
  COALESCE(SUM(CASE WHEN c.priority = 6 THEN c.end_ts - c.start_ts ELSE 0 END), 0) AS scheduler_ns,
  COALESCE(SUM(CASE WHEN c.priority = 7 THEN c.end_ts - c.start_ts ELSE 0 END), 0) AS cache_ns,
  COALESCE(SUM(CASE WHEN c.priority = 8 THEN c.end_ts - c.start_ts ELSE 0 END), 0) AS provider_residual_ns,
  COALESCE(SUM(CASE WHEN c.end_ts > c.start_ts AND (c.priority IS NULL OR c.priority = 9)
    THEN c.end_ts - c.start_ts ELSE 0 END), 0) AS unattributed_ns
FROM tokens t LEFT JOIN classified c USING (token_slice_id)
GROUP BY t.token_slice_id, t.request_id, t.token_index, t.n_tokens, t.start_ts, t.end_ts, t.wall_ns
ORDER BY t.start_ts;
