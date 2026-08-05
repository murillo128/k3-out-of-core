WITH
api_correlations AS MATERIALIZED (
  SELECT CAST(EXTRACT_ARG(arg_set_id, 'debug.correlation_id') AS INT) AS correlation_id
  FROM slice WHERE category = 'k3.cuda' AND name IN ('runtime_api', 'driver_api')
  GROUP BY correlation_id
)
SELECT name,
  CAST(EXTRACT_ARG(arg_set_id, 'debug.device_id') AS INT) AS device_id,
  CAST(EXTRACT_ARG(arg_set_id, 'debug.context_id') AS INT) AS context_id,
  CAST(EXTRACT_ARG(arg_set_id, 'debug.stream_id') AS INT) AS stream_id,
  CAST(EXTRACT_ARG(arg_set_id, 'debug.application_correlation_id') AS INT) AS application_correlation_id,
  COUNT(*) AS activities, SUM(dur) AS duration_sum_ns, MIN(ts) AS first_ts, MAX(ts + dur) AS last_ts,
  SUM(COALESCE(CAST(EXTRACT_ARG(arg_set_id, 'debug.bytes') AS INT), 0)) AS bytes,
  SUM(CASE WHEN name = 'kernel' THEN
    MAX(0, CAST(EXTRACT_ARG(arg_set_id, 'debug.submitted_ns') AS INT) -
      CAST(EXTRACT_ARG(arg_set_id, 'debug.queued_ns') AS INT)) ELSE 0 END) AS kernel_launch_queue_ns,
  SUM(CASE WHEN CAST(EXTRACT_ARG(arg_set_id, 'debug.correlation_id') AS INT) IN
    (SELECT correlation_id FROM api_correlations) THEN 1 ELSE 0 END) AS api_correlation_matches
FROM slice
WHERE category = 'k3.cuda' AND name != 'kernel_queued' AND dur >= 0
GROUP BY name, device_id, context_id, stream_id, application_correlation_id
ORDER BY first_ts;
