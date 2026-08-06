SELECT
  category,
  name,
  EXTRACT_ARG(arg_set_id, 'integrity_checked') AS integrity_checked,
  COUNT(*) AS executions,
  SUM(dur) AS total_ns,
  CAST(AVG(dur) AS INT) AS mean_ns,
  MAX(dur) AS max_ns
FROM slice
WHERE category IN ('k3.storage', 'k3.provider')
  AND name IN ('integrity_digest', 'integrity_finalize')
GROUP BY category, name, integrity_checked
ORDER BY category, name, integrity_checked;
