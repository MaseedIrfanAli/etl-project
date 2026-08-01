# Metrics Dictionary

| Metric | Type | Category | Formula / Meaning | Owner |
|---|---|---|---|---|
| `etl_records_processed_total` | Counter | Business | Total records successfully cleaned and loaded | Data Platform |
| `etl_records_failed_total` | Counter | Business + Technical | Records dropped due to schema drift or bad data | Data Platform |
| `etl_duplicate_records_total` | Counter | Technical | Records skipped because their ID was already seen this run | Data Eng |
| `etl_schema_drift_detected` | Gauge (0/1) | Technical | 1 if any record in this run had unexpected/missing fields | Data Eng |
| `etl_data_freshness_seconds` | Gauge | Business (SLA) | `now - newest_record_timestamp`; alerted when > 1800s | Business Stakeholders |
| `etl_job_duration_seconds` | Histogram | Technical | Wall-clock time of the full extract-transform-load run | Platform Ops |
| `etl_job_last_run_success` | Gauge (0/1) | Business + Technical | 1 if the last CronJob run completed without exception | Both |
| `kube_pod_container_status_restarts_total` | Counter (kube-state-metrics) | Technical | Pod restart count in the `etl` namespace | Platform Ops |
| `kube_job_status_start_time` | Gauge (kube-state-metrics) | Technical | Used to detect hung/stuck job runs | Platform Ops |

## Derived / dashboard-only metrics

- **Success rate %** = `etl_records_processed_total / (etl_records_processed_total + etl_records_failed_total)`
- **Failure rate (rolling 15m)** = used directly in `ETLHighFailureRate` alert
- **Estimated cost per run** = (pod CPU/memory requests × run duration) mapped to AKS node hourly cost — track manually in Grafana with a `text` panel until Azure Cost Management API is wired in

## Notes

- All metrics are pushed to Prometheus **Pushgateway** rather than scraped, because the ETL job runs as a short-lived CronJob pod and would likely exit before a scrape interval fires.
- Pushgateway metrics are **not automatically cleared** between runs — add a cleanup job or use grouping keys per run ID if you need per-run granularity instead of "last run" semantics.
