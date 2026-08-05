WITH
app_upids AS MATERIALIZED (
  SELECT DISTINCT th.upid
  FROM slice s JOIN thread_track tt ON tt.id = s.track_id JOIN thread th ON th.utid = tt.utid
  WHERE s.category GLOB 'k3.*' AND th.upid IS NOT NULL
),
app_threads AS MATERIALIZED (
  SELECT th.utid, th.tid, th.name AS thread_name, p.pid, p.name AS process_name
  FROM thread th JOIN process p ON p.upid = th.upid
  WHERE th.upid IN (SELECT upid FROM app_upids)
),
state_totals AS MATERIALIZED (
  SELECT ts.utid, ts.state, COUNT(*) AS intervals, SUM(CASE WHEN ts.dur > 0 THEN ts.dur ELSE 0 END) AS duration_ns
  FROM thread_state ts WHERE ts.utid IN (SELECT utid FROM app_threads)
  GROUP BY ts.utid, ts.state
),
sched_ordered AS MATERIALIZED (
  SELECT s.utid, s.cpu, LAG(s.cpu) OVER (PARTITION BY s.utid ORDER BY s.ts) AS prior_cpu
  FROM sched s WHERE s.utid IN (SELECT utid FROM app_threads)
),
migrations AS (
  SELECT utid, SUM(CASE WHEN prior_cpu IS NOT NULL AND cpu != prior_cpu THEN 1 ELSE 0 END) AS migrations
  FROM sched_ordered GROUP BY utid
)
SELECT a.pid, a.tid, a.process_name, a.thread_name, st.state, st.intervals, st.duration_ns,
  CASE WHEN st.state = 'R' THEN st.duration_ns ELSE 0 END AS runnable_delay_ns,
  CASE WHEN st.state IN ('Running', 'R+') THEN st.duration_ns ELSE 0 END AS running_ns,
  COALESCE(m.migrations, 0) AS migrations,
  (SELECT COUNT(*) FROM raw WHERE name = 'sys_enter_futex') AS futex_calls,
  (SELECT COUNT(*) FROM raw WHERE name = 'sys_enter_epoll_wait') AS epoll_wait_calls
FROM app_threads a JOIN state_totals st USING (utid) LEFT JOIN migrations m USING (utid)
ORDER BY a.pid, a.tid, st.duration_ns DESC;
