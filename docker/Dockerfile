# PayNest A7 container images (linux/arm64, Fargate Graviton). Build context = repo root (see /.dockerignore).
#
#   docker buildx build --platform linux/arm64 -f docker/Dockerfile --target <worker|api|mock-payroll|bootstrap> .
#
# Targets: worker (SQS consumer: pii_gate -> graph), api (reviewer API + queue UI + /admin/flags + /shadow),
#          act (send-time PII restore check; same deps as worker), mock-payroll (demo only), bootstrap (one-off RunTask).
# Rules: slim base, non-root (uid 10001), deps installed with uv from pinned requirements, no secrets or .env in the image
# (everything arrives as ECS environment / Secrets Manager secrets at run time), read-only-root-friendly (HOME=/tmp).

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE} AS base
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app UV_LINK_MODE=copy \
    HOME=/tmp A7_DATA_ROOM=/app/data-room A7_AWS_PROFILE=none LANGGRAPH_STRICT_MSGPACK=true
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin a7
WORKDIR /app

# ---- full runtime deps (api, worker, act) ---------------------------------------------------------------------------------
FROM base AS runtime-deps
COPY docker/requirements-runtime.txt /tmp/requirements.txt
RUN uv pip install --system --no-cache -r /tmp/requirements.txt && rm /tmp/requirements.txt

FROM runtime-deps AS runtime
COPY solution/a7 /app/solution/a7
COPY solution/db /app/solution/db
COPY solution/ingest /app/solution/ingest
COPY data-room /app/data-room
USER 10001

FROM runtime AS worker
CMD ["python", "-m", "solution.a7.sqs_worker"]

FROM runtime AS api
EXPOSE 8080
# /health opens a DB connection, so it doubles as the ALB health check.
CMD ["uvicorn", "solution.a7.api.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]

# Act: the only image that opens the PII vault. Same code, different task role and secrets.
FROM runtime AS act
CMD ["python", "-m", "solution.a7.sender", "--help"]

# ---- mock payroll (demo only) ---------------------------------------------------------------------------------------------
FROM base AS mock-payroll
COPY docker/requirements-mock.txt /tmp/requirements.txt
RUN uv pip install --system --no-cache -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY solution/a7/mock_payroll /app/solution/a7/mock_payroll
COPY data-room/data/customers.csv /app/data-room/data/customers.csv
COPY data-room/integrations/statutory_rules.json /app/data-room/integrations/statutory_rules.json
USER 10001
EXPOSE 8080
CMD ["uvicorn", "solution.a7.mock_payroll.app:app", "--host", "0.0.0.0", "--port", "8080"]

# ---- one-off bootstrap / ingest (RunTask) ----------------------------------------------------------------------------------
FROM base AS bootstrap
COPY docker/requirements-bootstrap.txt /tmp/requirements.txt
RUN uv pip install --system --no-cache -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY solution/a7/db.py solution/a7/config.py solution/a7/embed.py /app/solution/a7/
COPY solution/db /app/solution/db
COPY solution/ingest /app/solution/ingest
COPY data-room /app/data-room
USER 10001
CMD ["python", "-m", "solution.db.provision"]
