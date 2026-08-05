WITH
api_correlations AS MATERIALIZED (
  SELECT CAST(EXTRACT_ARG(arg_set_id, 'debug.correlation_id') AS INT) AS correlation_id
  FROM slice
  WHERE category = 'k3.cuda' AND name IN ('runtime_api', 'driver_api')
  GROUP BY correlation_id
),
graph_ids AS MATERIALIZED (
  SELECT CAST(EXTRACT_ARG(arg_set_id, 'debug.graph_id') AS INT) AS application_id, ts, ts + dur AS end_ts
  FROM slice WHERE category = 'k3.graph' AND name = 'graph_compute' AND dur >= 0
),
flight_ids AS MATERIALIZED (
  SELECT CAST(EXTRACT_ARG(arg_set_id, 'debug.flight_id') AS INT) AS application_id, ts, ts + dur AS end_ts
  FROM slice WHERE category = 'k3.scheduler' AND name = 'flight' AND dur >= 0
),
request_ids AS MATERIALIZED (
  SELECT 72057594037927936 + CAST(EXTRACT_ARG(arg_set_id, 'debug.request_id') AS INT) AS application_id
  FROM slice WHERE category = 'k3.request' AND name = 'request_begin'
),
application_ids AS MATERIALIZED (
  SELECT application_id FROM graph_ids
  UNION SELECT application_id FROM flight_ids
  UNION SELECT application_id FROM request_ids
),
cuda_activity AS MATERIALIZED (
  SELECT id, track_id, ts, dur, name,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.correlation_id') AS INT) AS correlation_id,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.application_correlation_id') AS INT) AS application_id
  FROM slice WHERE category = 'k3.cuda' AND name != 'kernel_queued'
),
ordered_cuda AS (
  SELECT id, track_id, ts, LAG(ts) OVER (PARTITION BY track_id ORDER BY id) AS prior_ts
  FROM cuda_activity
),
anchors AS (
  SELECT
    MAX(CASE WHEN name = 'trace_session_start' THEN ts END) AS start_ts,
    MAX(CASE WHEN name = 'trace_session_start' THEN CAST(EXTRACT_ARG(arg_set_id, 'debug.clock_monotonic_raw_ns') AS INT) END) AS start_raw,
    MAX(CASE WHEN name = 'trace_session_stop' THEN ts END) AS stop_ts,
    MAX(CASE WHEN name = 'trace_session_stop' THEN CAST(EXTRACT_ARG(arg_set_id, 'debug.clock_monotonic_raw_ns') AS INT) END) AS stop_raw
  FROM slice WHERE category = 'k3.lifecycle'
),
loss AS (
  SELECT COALESCE(SUM(ABS(value)), 0) AS total
  FROM stats
  WHERE severity = 'data_loss'
     OR name IN ('ftrace_cpu_dropped_events_delta', 'ftrace_cpu_commit_overrun_delta',
       'traced_buf_incremental_sequences_dropped', 'traced_buf_sequence_packet_loss',
       'traced_buf_trace_writer_packet_loss', 'traced_final_flush_failed', 'traced_flushes_failed',
       'track_event_dropped_packets_outside_of_range_of_interest')
),
flows_checked AS (
  SELECT f.id, so.category AS out_category, so.name AS out_name,
    si.category AS in_category, si.name AS in_name,
    EXTRACT_ARG(so.arg_set_id, 'debug.flight_id') AS flight_id
  FROM flow f JOIN slice so ON so.id = f.slice_out JOIN slice si ON si.id = f.slice_in
)
SELECT
  (SELECT COUNT(*) FROM slice) AS slice_count,
  (SELECT COUNT(DISTINCT category) FROM slice WHERE category GLOB 'k3.*') AS application_category_count,
  (SELECT COUNT(*) FROM slice WHERE category = 'k3.lifecycle' AND name = 'trace_session_start') AS trace_start_count,
  (SELECT COUNT(*) FROM slice WHERE category = 'k3.lifecycle' AND name = 'trace_session_stop') AS trace_stop_count,
  (SELECT COUNT(*) FROM slice WHERE category = 'k3.lifecycle' AND name IN
    ('expert_runtime_shutdown', 'provider_teardown', 'async_io_shutdown', 'transfer_ring_surrender',
     'cold_cache_surrender', 'scheduler_shutdown')) AS teardown_slice_count,
  (SELECT COUNT(*) FROM slice WHERE category GLOB 'k3.*' AND dur < 0) AS incomplete_application_slices,
  (SELECT COUNT(*) FROM ordered_cuda WHERE prior_ts IS NOT NULL AND ts < prior_ts) AS cuda_packet_order_regressions,
  (SELECT COUNT(*) FROM cuda_activity WHERE dur < 0) AS incomplete_cuda_slices,
  (SELECT COUNT(*) FROM cuda_activity WHERE dur < 0 OR ts < 0) AS invalid_cuda_intervals,
  (SELECT COUNT(*) FROM cuda_activity WHERE name = 'kernel') AS cuda_kernel_count,
  (SELECT COUNT(*) FROM cuda_activity WHERE name = 'memcpy') AS cuda_memcpy_count,
  (SELECT COUNT(*) FROM cuda_activity WHERE name = 'synchronization') AS cuda_sync_count,
  (SELECT COUNT(*) FROM cuda_activity c WHERE c.name = 'kernel'
    AND c.correlation_id IN (SELECT correlation_id FROM api_correlations)) AS cuda_kernel_api_matches,
  (SELECT COUNT(*) FROM cuda_activity c WHERE c.name = 'memcpy'
    AND c.correlation_id IN (SELECT correlation_id FROM api_correlations)) AS cuda_memcpy_api_matches,
  (SELECT COUNT(*) FROM cuda_activity c WHERE c.application_id != 0
    AND c.application_id IN (SELECT application_id FROM application_ids)) AS cuda_application_matches,
  (SELECT COUNT(*) FROM cuda_activity WHERE application_id != 0) AS cuda_application_nonzero,
  (SELECT COUNT(*) FROM cuda_activity c WHERE c.name = 'kernel'
    AND c.application_id IN (SELECT application_id FROM graph_ids)) AS graph_kernel_matches,
  (SELECT COUNT(*) FROM cuda_activity c WHERE c.name = 'memcpy'
    AND c.application_id IN (SELECT application_id FROM flight_ids)) AS flight_memcpy_matches,
  ((SELECT COUNT(*) FROM cuda_activity c WHERE c.name = 'kernel'
    AND c.application_id IN (SELECT application_id FROM graph_ids)
    AND NOT EXISTS (SELECT 1 FROM graph_ids g WHERE g.application_id = c.application_id
      AND c.ts >= g.ts - 1000000 AND c.ts + c.dur <= g.end_ts + 1000000))
   +
   (SELECT COUNT(*) FROM cuda_activity c WHERE c.name = 'memcpy'
    AND c.application_id IN (SELECT application_id FROM flight_ids)
    AND NOT EXISTS (SELECT 1 FROM flight_ids f WHERE f.application_id = c.application_id
      AND c.ts >= f.ts - 1000000 AND c.ts + c.dur <= f.end_ts + 1000000))
  ) AS cuda_application_clock_mismatches,
  (SELECT COUNT(*) FROM flows_checked) AS flow_count,
  (SELECT COUNT(*) FROM flows_checked WHERE out_category != 'k3.scheduler' OR out_name != 'flight_dispatch'
    OR in_category != 'k3.scheduler' OR in_name != 'flight_terminal' OR flight_id IS NULL) AS invalid_flows,
  (SELECT COUNT(DISTINCT snapshot_id) FROM clock_snapshot WHERE clock_name = 'BOOTTIME'
    AND snapshot_id IN (SELECT snapshot_id FROM clock_snapshot WHERE clock_name = 'MONOTONIC_RAW')) AS common_clock_snapshots,
  (SELECT ABS((stop_ts - start_ts) - (stop_raw - start_raw)) FROM anchors) AS clock_anchor_residual_ns,
  (SELECT total FROM loss) AS required_source_loss,
  (SELECT COUNT(*) FROM raw WHERE name = 'sched_switch') AS sched_switch_count,
  (SELECT COUNT(*) FROM raw WHERE name IN ('sched_waking', 'sched_wakeup')) AS sched_wake_count,
  (SELECT COUNT(*) FROM raw WHERE name GLOB 'sys_enter_*') AS syscall_enter_count,
  (SELECT COUNT(*) FROM raw WHERE name IN ('sys_enter_pread64', 'sys_enter_io_uring_enter')) AS storage_syscall_count,
  (SELECT COUNT(*) FROM raw WHERE name IN ('block_rq_issue', 'block_rq_complete', 'block_bio_queue')) AS block_event_count,
  (SELECT COUNT(*) FROM raw WHERE name IN ('page_fault_user', 'page_fault_kernel')) AS fault_event_count,
  (SELECT COUNT(*) FROM raw WHERE name IN ('mm_filemap_add_to_page_cache', 'mm_filemap_delete_from_page_cache')) AS filemap_event_count,
  (SELECT COUNT(*) FROM counter c JOIN counter_track t ON t.id = c.track_id WHERE t.type = 'process_memory') AS process_stat_count,
  (SELECT COUNT(*) FROM counter c JOIN counter_track t ON t.id = c.track_id WHERE t.type IN ('cpustat', 'meminfo')) AS system_stat_count,
  (SELECT COALESCE(MAX(CAST(EXTRACT_ARG(arg_set_id, 'debug.cupti_errors') AS INT)), -1)
    FROM slice WHERE category = 'k3.lifecycle' AND name = 'trace_session_stop') AS cupti_errors,
  (SELECT COALESCE(MAX(CAST(EXTRACT_ARG(arg_set_id, 'debug.cupti_dropped_records') AS INT)), -1)
    FROM slice WHERE category = 'k3.lifecycle' AND name = 'trace_session_stop') AS cupti_dropped_records,
  (SELECT COALESCE(MAX(CAST(EXTRACT_ARG(arg_set_id, 'debug.cupti_unknown_timestamps') AS INT)), -1)
    FROM slice WHERE category = 'k3.lifecycle' AND name = 'trace_session_stop') AS cupti_unknown_timestamps,
  (SELECT COALESCE(MAX(CAST(EXTRACT_ARG(arg_set_id, 'debug.cupti_peak_total_bytes') AS INT)), -1)
    FROM slice WHERE category = 'k3.lifecycle' AND name = 'trace_session_stop') AS cupti_peak_total_bytes,
  (SELECT COALESCE(MAX(CAST(EXTRACT_ARG(arg_set_id, 'debug.cupti_retained_capacity_bytes') AS INT)), -1)
    FROM slice WHERE category = 'k3.lifecycle' AND name = 'trace_session_stop') AS cupti_retained_capacity_bytes;
