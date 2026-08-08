WITH
cuda_activity AS MATERIALIZED (
  SELECT * FROM slice WHERE category = 'k3.cuda'
),
loss AS (
  SELECT COALESCE(SUM(ABS(value)), 0) AS total
  FROM stats
  WHERE severity = 'data_loss'
     OR name IN ('traced_buf_incremental_sequences_dropped', 'traced_buf_sequence_packet_loss',
       'traced_buf_trace_writer_packet_loss', 'traced_final_flush_failed', 'traced_flushes_failed',
       'track_event_dropped_packets_outside_of_range_of_interest')
),
stop_diagnostics AS (
  SELECT
    CAST(EXTRACT_ARG(arg_set_id, 'debug.cupti_errors') AS INT) AS cupti_errors,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.cupti_dropped_records') AS INT) AS cupti_dropped_records,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.cupti_unknown_timestamps') AS INT) AS cupti_unknown_timestamps,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.cupti_unmatched_correlations') AS INT) AS cupti_unmatched_correlations,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.cupti_kernel_records') AS INT) AS cupti_kernel_records,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.cupti_memcpy_records') AS INT) AS cupti_memcpy_records,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.cupti_synchronization_records') AS INT) AS cupti_sync_records,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.cupti_unsupported_records') AS INT) AS cupti_unsupported_records,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.cupti_active_buffer_bytes_at_close') AS INT)
      AS cupti_active_buffer_bytes_at_close,
    CAST(EXTRACT_ARG(arg_set_id, 'debug.cupti_peak_total_bytes') AS INT) AS cupti_peak_total_bytes
  FROM slice
  WHERE category = 'k3.lifecycle' AND name = 'trace_session_stop'
)
SELECT
  (SELECT COUNT(*) FROM slice) AS slice_count,
  (SELECT COUNT(*) FROM slice WHERE category = 'k3.lifecycle' AND name = 'decode_window_start') AS window_start_count,
  (SELECT COUNT(*) FROM slice WHERE category = 'k3.lifecycle' AND name = 'decode_window_end') AS window_end_count,
  (SELECT COUNT(*) FROM slice WHERE category = 'k3.lifecycle' AND name = 'trace_session_start') AS session_start_count,
  (SELECT COUNT(*) FROM slice WHERE category = 'k3.lifecycle' AND name = 'trace_session_stop') AS session_stop_count,
  (SELECT COUNT(*) FROM slice WHERE category = 'k3.graph' AND name = 'expert_layer_execution' AND dur > 0)
    AS complete_routed_layer_intervals,
  (SELECT COUNT(*) FROM slice WHERE category GLOB 'k3.*' AND name IN
    ('model_create', 'provider_initialize', 'storage_initialize', 'context_create')) AS initialization_slice_count,
  (SELECT COUNT(*) FROM cuda_activity WHERE name = 'kernel') AS cuda_kernel_count,
  (SELECT COUNT(*) FROM cuda_activity WHERE name = 'memcpy') AS cuda_memcpy_count,
  (SELECT COUNT(*) FROM cuda_activity WHERE name = 'synchronization') AS cuda_sync_count,
  (SELECT COUNT(*) FROM cuda_activity WHERE name IN ('runtime_api', 'driver_api', 'memset', 'kernel_queued'))
    AS forbidden_cuda_slice_count,
  (SELECT COUNT(*) FROM cuda_activity WHERE name NOT IN ('kernel', 'memcpy', 'synchronization'))
    AS unexpected_cuda_slice_count,
  (SELECT COUNT(*) FROM cuda_activity
    WHERE EXTRACT_ARG(arg_set_id, 'debug.application_correlation_id') IS NOT NULL)
    AS external_correlation_argument_count,
  (SELECT COUNT(*) FROM cuda_activity WHERE dur < 0 OR ts < 0) AS invalid_cuda_interval_count,
  (SELECT COUNT(*) FROM cuda_activity WHERE name = 'memcpy'
    AND CAST(EXTRACT_ARG(arg_set_id, 'debug.copy_kind') AS INT) = 1) AS h2d_memcpy_count,
  (SELECT COUNT(*) FROM cuda_activity WHERE name = 'memcpy'
    AND CAST(EXTRACT_ARG(arg_set_id, 'debug.copy_kind') AS INT) = 2) AS d2h_memcpy_count,
  (SELECT total FROM loss) AS trace_data_loss,
  (SELECT cupti_errors FROM stop_diagnostics) AS cupti_errors,
  (SELECT cupti_dropped_records FROM stop_diagnostics) AS cupti_dropped_records,
  (SELECT cupti_unknown_timestamps FROM stop_diagnostics) AS cupti_unknown_timestamps,
  (SELECT cupti_unmatched_correlations FROM stop_diagnostics) AS cupti_unmatched_correlations,
  (SELECT cupti_kernel_records FROM stop_diagnostics) AS cupti_kernel_records,
  (SELECT cupti_memcpy_records FROM stop_diagnostics) AS cupti_memcpy_records,
  (SELECT cupti_sync_records FROM stop_diagnostics) AS cupti_sync_records,
  (SELECT cupti_unsupported_records FROM stop_diagnostics) AS cupti_unsupported_records,
  (SELECT cupti_active_buffer_bytes_at_close FROM stop_diagnostics) AS cupti_active_buffer_bytes_at_close,
  (SELECT cupti_peak_total_bytes FROM stop_diagnostics) AS cupti_peak_total_bytes;
