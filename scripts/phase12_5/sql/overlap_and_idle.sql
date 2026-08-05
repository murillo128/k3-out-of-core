WITH
tokens AS MATERIALIZED (
  SELECT id AS token_slice_id, ts AS start_ts, ts + dur AS end_ts, dur AS wall_ns,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.request_id') AS INT) AS request_id,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.token_index') AS INT) AS token_index
  FROM slice WHERE category = 'k3.request' AND name = 'process_ubatch' AND dur > 0
),
intervals AS MATERIALIZED (
  SELECT t.token_slice_id, MAX(t.start_ts, s.ts) AS start_ts, MIN(t.end_ts, s.ts + s.dur) AS end_ts,
    CASE WHEN s.category = 'k3.cuda' AND s.name IN ('kernel', 'memcpy', 'memset', 'synchronization')
      THEN 'gpu' ELSE 'storage' END AS kind
  FROM tokens t JOIN slice s ON s.dur > 0 AND s.ts < t.end_ts AND s.ts + s.dur > t.start_ts
  WHERE s.category = 'k3.storage' OR (s.category = 'k3.cuda'
    AND s.name IN ('kernel', 'memcpy', 'memset', 'synchronization'))
),
events AS MATERIALIZED (
  SELECT token_slice_id, start_ts AS ts, CASE WHEN kind = 'gpu' THEN 1 ELSE 0 END AS gpu_delta,
    CASE WHEN kind = 'storage' THEN 1 ELSE 0 END AS storage_delta FROM intervals
  UNION ALL
  SELECT token_slice_id, end_ts, CASE WHEN kind = 'gpu' THEN -1 ELSE 0 END,
    CASE WHEN kind = 'storage' THEN -1 ELSE 0 END FROM intervals
  UNION ALL SELECT token_slice_id, start_ts, 0, 0 FROM tokens
  UNION ALL SELECT token_slice_id, end_ts, 0, 0 FROM tokens
),
event_points AS MATERIALIZED (
  SELECT token_slice_id, ts, SUM(gpu_delta) AS gpu_delta, SUM(storage_delta) AS storage_delta
  FROM events GROUP BY token_slice_id, ts
),
sweep AS MATERIALIZED (
  SELECT token_slice_id, ts,
    SUM(gpu_delta) OVER window AS gpu,
    SUM(storage_delta) OVER window AS storage
  FROM event_points
  WINDOW window AS (PARTITION BY token_slice_id ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
),
flags AS MATERIALIZED (
  SELECT token_slice_id, ts AS start_ts, LEAD(ts) OVER (PARTITION BY token_slice_id ORDER BY ts) AS end_ts,
    gpu, storage FROM sweep
)
SELECT t.request_id, t.token_index, t.start_ts, t.end_ts, t.wall_ns,
  SUM(CASE WHEN f.gpu > 0 THEN f.end_ts - f.start_ts ELSE 0 END) AS gpu_busy_ns,
  SUM(CASE WHEN f.storage > 0 THEN f.end_ts - f.start_ts ELSE 0 END) AS storage_busy_ns,
  SUM(CASE WHEN f.gpu > 0 AND f.storage > 0 THEN f.end_ts - f.start_ts ELSE 0 END) AS gpu_storage_overlap_ns,
  SUM(CASE WHEN f.gpu = 0 AND f.storage = 0 THEN f.end_ts - f.start_ts ELSE 0 END) AS gpu_storage_idle_ns
FROM tokens t JOIN flags f USING (token_slice_id)
GROUP BY t.token_slice_id, t.request_id, t.token_index, t.start_ts, t.end_ts, t.wall_ns
ORDER BY t.start_ts;
