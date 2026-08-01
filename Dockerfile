FROM python:3.12-slim

# Run as non-root (technical edge case: default root containers fail
# Pod Security Admission "restricted" policy on AKS)
RUN useradd -m -u 1000 etluser

WORKDIR /app

COPY src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/etl_job.py .

RUN mkdir -p /data/input /data/output && chown -R etluser:etluser /app /data

USER etluser

# Defaults; override via ConfigMap/env in Kubernetes
ENV PUSHGATEWAY_URL=prometheus-pushgateway.monitoring:9091 \
    JOB_NAME=etl_job \
    SOURCE_PATH=/data/input/source.json \
    MAX_RETRIES=3 \
    SLA_FRESHNESS_SECONDS=1800

ENTRYPOINT ["python3", "etl_job.py"]
