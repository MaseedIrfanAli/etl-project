# ETL Pipeline Runbook

Log every incident here — simulated or real. Format: Symptom → Root Cause → Fix → Prevention.

---

## 1. Schema Drift

**Symptom:** `ETLSchemaDriftDetected` alert fires; `etl_records_failed_total` increases.
**Root Cause:** Upstream source added/removed a field without notice (e.g. a new `extra_field`).
**Fix:** Inspect the raw source payload, update `EXPECTED_SCHEMA` in `etl_job.py` if the change is intentional and permanent.
**Prevention:** Add a schema contract/versioning agreement with the upstream team; consider a schema registry for stricter enforcement.

---

## 2. Duplicate Records

**Symptom:** `etl_duplicate_records_total` increases; downstream record counts don't match source counts.
**Root Cause:** Source system re-sent records (e.g. retry on their end, or overlapping export windows).
**Fix:** Confirmed dedup logic (by `id`) already drops these safely — verify no data loss by checking `etl_records_failed_total` didn't also spike.
**Prevention:** Ask upstream for idempotency keys; consider windowed dedup if IDs aren't guaranteed unique across days.

---

## 3. Unparseable Timestamp / Timezone Bug

**Symptom:** Record dropped silently, logged as "Unparseable timestamp."
**Root Cause:** Source sent a non-ISO8601 string, or a naive (no-timezone) timestamp.
**Fix:** Add a fallback parser for common alternate formats before dropping the record.
**Prevention:** Enforce ISO8601 + UTC in the source contract; add a canary test that fails the build if timestamp format changes.

---

## 4. Data Freshness SLA Breach

**Symptom:** `ETLDataFreshnessSLABreach` alert fires.
**Root Cause:** Could be (a) source system delay, (b) CronJob didn't run (check `startingDeadlineSeconds`), or (c) job ran but processed stale data.
**Fix:** Check CronJob history (`kubectl get jobs -n etl`) to confirm the job actually ran on schedule.
**Prevention:** Alert separately on "job didn't run" vs "job ran but data is stale" — these are different root causes with the same symptom.

---

## 5. Pod OOMKilled

**Symptom:** Pod status shows `OOMKilled`; job fails with `backoffLimit` exceeded.
**Root Cause:** Source file grew larger than expected, exceeding the 512Mi memory limit.
**Fix:** Bump `resources.limits.memory` in `03-cronjob.yaml`; consider streaming/chunked processing instead of loading the full file into memory.
**Prevention:** Add a `etl_input_file_size_bytes` metric and alert before it becomes a crash.

---

## 6. Pushgateway Unreachable

**Symptom:** Job logs show "Failed to push metrics to..." but the ETL run itself succeeded.
**Root Cause:** Network policy blocking egress from `etl` namespace to `monitoring` namespace, or Pushgateway pod down.
**Fix:** `kubectl get pods -n monitoring | grep pushgateway`; check NetworkPolicy allows `etl` → `monitoring:9091`.
**Prevention:** Keep metrics-push failures non-fatal to the data pipeline (already implemented) but alert on log pattern via Azure Log Analytics query.

---

## 7. Key Vault Secret Rotation Mid-Run

**Symptom:** Job fails partway through with an auth error against Storage.
**Root Cause:** Connection string rotated in Key Vault while the pod's mounted secret was stale (CSI driver polling interval).
**Fix:** Re-run the job; confirm CSI driver's secret rotation poll interval is shorter than your rotation cadence.
**Prevention:** Use Managed Identity + RBAC on Storage instead of connection strings where possible, to avoid rotation entirely.

---

## 8. Node Drain / Autoscale During Job Run

**Symptom:** Pod terminated mid-run with no clear application-level error.
**Root Cause:** Cluster autoscaler scaled down the node the pod was running on.
**Fix:** Check `kubectl describe pod` for `Evicted` or node drain events around the failure time.
**Prevention:** Add a `PodDisruptionBudget` is not applicable to Jobs, but set `terminationGracePeriodSeconds` and design the job to be safely re-runnable (idempotent) so a restart is harmless.

---

## Template for new entries

```
## N. <Short title>

**Symptom:**
**Root Cause:**
**Fix:**
**Prevention:**
```
