# Shared image for both the `backend` (API, worker_role=api) and `worker`
# (ingestion_worker, worker_role=ingestion_worker) Docker Compose services
# -- both run the exact same Python codebase, differing only in the
# `command:` each service's compose entry specifies (uvicorn vs. a Celery
# worker process) and the WORKER_ROLE environment variable each is given.
# One image, not two, avoids maintaining a second Dockerfile that could
# drift from this one for no real reason -- the two processes share every
# dependency.
#
# Multi-stage: a `builder` stage compiles/installs into a virtualenv,
# and the final stage copies only that virtualenv plus the application
# source, not the build toolchain -- a smaller final image, and no
# compiler present in the container that would eventually run untrusted
# scanner output or model responses.

FROM python:3.12-slim AS builder

WORKDIR /build

# Only pyproject.toml first, so Docker's layer cache is invalidated by a
# dependency change, not by every application source-code change.
COPY pyproject.toml ./
COPY app ./app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

FROM python:3.12-slim AS runtime

# A non-root user to run as -- the same "never run as root" posture
# already enforced in-process for scanner subprocess execution
# (app/scanner_engine/base_scanner.py's NonRootExecutionError guard),
# applied here at the container level too: belt-and-suspenders, not a
# substitute for that in-process check, which still applies regardless
# of what the container's own UID happens to be.
RUN groupadd --system app && useradd --system --gid app --create-home app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

RUN chown -R app:app /app
USER app

EXPOSE 8000

# No default CMD: `docker-compose.yml` gives each service (`backend`,
# `worker`) its own explicit `command:` -- uvicorn for one, a Celery
# worker process for the other -- since a single default here would
# have to be wrong for at least one of them.
