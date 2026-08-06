WITH
requests AS MATERIALIZED (
  SELECT id, ts, ts + dur AS end_ts, dur AS request_wall_ns,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.request_slot') AS INT) AS request_slot,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.request_generation') AS INT) AS request_generation,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.request_ordinal') AS INT) AS request_ordinal,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.layer') AS INT) AS layer,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.original_expert_id') AS INT) AS original_expert_id,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.operation_count') AS INT) AS operation_count,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.bytes') AS INT) AS completed_bytes,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.used_io_uring') AS INT) AS used_io_uring,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.native_error') AS INT) AS native_error
  FROM slice WHERE category = 'k3.storage' AND name = 'read_request' AND dur >= 0
),
starts AS MATERIALIZED (
  SELECT CAST(EXTRACT_ARG(arg_set_id, 'debug.request_slot') AS INT) AS request_slot,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.request_generation') AS INT) AS request_generation,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.request_ordinal') AS INT) AS request_ordinal,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.queue_wait_us') AS INT) * 1000 AS queue_wait_ns
  FROM slice WHERE category = 'k3.storage' AND name = 'request_start'
),
operations AS MATERIALIZED (
  SELECT CAST(EXTRACT_ARG(arg_set_id, 'debug.request_slot') AS INT) AS request_slot,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.request_generation') AS INT) AS request_generation,
    COUNT(*) AS observed_operations, SUM(dur) AS operation_duration_sum_ns,
    MAX(ts + dur) - MIN(ts) AS operation_service_wall_ns,
    SUM(CAST(EXTRACT_ARG(arg_set_id, 'debug.completed_bytes') AS INT)) AS operation_completed_bytes,
    SUM(CASE WHEN CAST(EXTRACT_ARG(arg_set_id, 'debug.io_uring') AS INT) != 0 THEN 1 ELSE 0 END) AS io_uring_operations,
    SUM(CASE WHEN CAST(EXTRACT_ARG(arg_set_id, 'debug.native_result') AS INT) != 0 THEN 1 ELSE 0 END) AS failed_operations
  FROM slice WHERE category = 'k3.storage' AND name = 'read_operation' AND dur >= 0
  GROUP BY request_slot, request_generation
)
SELECT r.request_slot, r.request_generation, r.request_ordinal, r.layer, r.original_expert_id,
  r.ts AS request_start_ts, r.end_ts AS request_end_ts, r.request_wall_ns,
  s.queue_wait_ns, r.operation_count, o.observed_operations, o.operation_duration_sum_ns,
  o.operation_service_wall_ns, r.completed_bytes, o.operation_completed_bytes,
  r.used_io_uring, o.io_uring_operations, r.native_error, o.failed_operations,
  (SELECT COUNT(*) FROM raw WHERE name = 'sys_enter_pread64') AS observed_pread64_calls,
  (SELECT COUNT(*) FROM raw WHERE name = 'sys_enter_io_uring_enter') AS observed_io_uring_enter_calls,
  (SELECT COUNT(*) FROM raw WHERE name IN ('block_rq_issue', 'block_rq_complete')) AS observed_block_events
FROM requests r LEFT JOIN starts s USING (request_slot, request_generation, request_ordinal)
LEFT JOIN operations o USING (request_slot, request_generation)
ORDER BY r.ts;
