"""
ETL Job with Prometheus instrumentation.

Extract -> Transform -> Load, with:
- structured JSON logging
- retry with exponential backoff
- custom Prometheus metrics (business + technical)
- basic edge-case detection (schema drift, duplicates, staleness)

Metrics are pushed to a Prometheus Pushgateway because this job runs as a
Kubernetes CronJob (short-lived pod), so a scrape-based /metrics endpoint
would not be reliably scraped before the pod exits.
"""

import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, push_to_gateway

# ---------------------------------------------------------------------------
# Config (env-driven so it maps cleanly to a Kubernetes ConfigMap/Secret)
# ---------------------------------------------------------------------------
PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "prometheus-pushgateway.monitoring:9091")
JOB_NAME = os.getenv("JOB_NAME", "etl_job")
SOURCE_PATH = os.getenv("SOURCE_PATH", "/data/input/source.json")
EXPECTED_SCHEMA = {"id", "timestamp", "value", "region"}
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
SLA_FRESHNESS_SECONDS = int(os.getenv("SLA_FRESHNESS_SECONDS", "1800"))  # 30 min

# ---------------------------------------------------------------------------
# Structured logging (JSON) - makes logs queryable in Azure Log Analytics
# ---------------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "job": JOB_NAME,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


logger = logging.getLogger(JOB_NAME)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
registry = CollectorRegistry()

records_processed = Counter(
    "etl_records_processed_total", "Total records successfully processed", registry=registry
)
records_failed = Counter(
    "etl_records_failed_total", "Total records that failed processing", registry=registry
)
duplicate_records = Counter(
    "etl_duplicate_records_total", "Total duplicate records detected", registry=registry
)
schema_drift_detected = Gauge(
    "etl_schema_drift_detected", "1 if schema drift detected in this run, else 0", registry=registry
)
data_freshness_seconds = Gauge(
    "etl_data_freshness_seconds", "Age of the newest record processed, in seconds", registry=registry
)
job_duration_seconds = Histogram(
    "etl_job_duration_seconds", "Wall-clock duration of the ETL job run", registry=registry
)
job_success = Gauge(
    "etl_job_last_run_success", "1 if the last run succeeded, else 0", registry=registry
)


# ---------------------------------------------------------------------------
# Retry decorator with exponential backoff
# ---------------------------------------------------------------------------
def retry_with_backoff(max_retries=3, base_delay=1.0):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    attempt += 1
                    if attempt > max_retries:
                        logger.error(f"{fn.__name__} failed after {max_retries} retries: {exc}")
                        raise
                    delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                    logger.warning(f"{fn.__name__} failed (attempt {attempt}/{max_retries}): {exc}. Retrying in {delay:.1f}s")
                    time.sleep(delay)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------
@retry_with_backoff(max_retries=MAX_RETRIES)
def extract(path):
    """Read source data. Raises if the file is missing or unreadable
    (simulates a source-system outage / network egress edge case)."""
    logger.info(f"Extracting from {path}")
    with open(path, "r") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Source data must be a JSON list of records")
    return data


# ---------------------------------------------------------------------------
# Transform  (this is where most real-world edge cases live)
# ---------------------------------------------------------------------------
def transform(records):
    seen_ids = set()
    clean_records = []
    newest_ts = None
    drift_flagged = False

    for rec in records:
        record_keys = set(rec.keys())

        # Edge case: schema drift (unexpected fields missing/added)
        if record_keys != EXPECTED_SCHEMA:
            missing = EXPECTED_SCHEMA - record_keys
            extra = record_keys - EXPECTED_SCHEMA
            logger.warning(f"Schema drift on record {rec.get('id')}: missing={missing} extra={extra}")
            drift_flagged = True
            records_failed.inc()
            continue

        # Edge case: duplicate records
        if rec["id"] in seen_ids:
            logger.warning(f"Duplicate record detected: id={rec['id']}")
            duplicate_records.inc()
            continue
        seen_ids.add(rec["id"])

        # Edge case: bad/unparseable timestamp (e.g. timezone bug)
        try:
            ts = datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            logger.warning(f"Unparseable timestamp on record {rec.get('id')}: {rec.get('timestamp')}")
            records_failed.inc()
            continue

        if newest_ts is None or ts > newest_ts:
            newest_ts = ts

        clean_records.append(rec)
        records_processed.inc()

    schema_drift_detected.set(1 if drift_flagged else 0)

    if newest_ts:
        freshness = (datetime.now(timezone.utc) - newest_ts).total_seconds()
        data_freshness_seconds.set(freshness)
        if freshness > SLA_FRESHNESS_SECONDS:
            logger.warning(f"Data freshness SLA breached: {freshness:.0f}s > {SLA_FRESHNESS_SECONDS}s")

    return clean_records


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------
@retry_with_backoff(max_retries=MAX_RETRIES)
def load(records, destination="/data/output/clean.json"):
    """Write transformed records. In production this would push to
    ADLS/Blob Storage via azure-storage-blob SDK."""
    logger.info(f"Loading {len(records)} records to {destination}")
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with open(destination, "w") as f:
        json.dump(records, f, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run():
    start = time.time()
    success = False
    try:
        raw = extract(SOURCE_PATH)
        clean = transform(raw)
        load(clean)
        success = True
        logger.info(f"ETL run complete: {len(clean)}/{len(raw)} records loaded")
    except Exception:
        logger.exception("ETL run failed")
        raise
    finally:
        duration = time.time() - start
        job_duration_seconds.observe(duration)
        job_success.set(1 if success else 0)
        try:
            push_to_gateway(PUSHGATEWAY_URL, job=JOB_NAME, registry=registry)
        except Exception as exc:
            # Pushgateway unreachable should not crash the ETL job itself,
            # but must be visible in logs (technical edge case: metrics
            # pipeline failure is a separate concern from data pipeline failure)
            logger.error(f"Failed to push metrics to {PUSHGATEWAY_URL}: {exc}")


if __name__ == "__main__":
    run()
