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

# Whether to install the Phase 5 RAG dependency stack (`sentence-
# transformers` + `qdrant-client`, backend/pyproject.toml's `rag`
# optional-dependency group) into this image -- "true" for the `worker`
# service only (docker-compose.yml's `worker` build args), never for
# `backend`/api, which never constructs either adapter (see
# app/workers/tasks.py's `_build_analysis_service` -- imported lazily,
# only inside ingestion_worker's own composition root). Defaults to
# "false" so an unmodified `docker build` (or any build that doesn't
# pass this arg) gets the smaller, RAG-free image -- the safer default.
ARG INSTALL_RAG_DEPENDENCIES=false

# Only pyproject.toml first, so Docker's layer cache is invalidated by a
# dependency change, not by every application source-code change.
COPY pyproject.toml ./
COPY app ./app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip
# CPU-only PyTorch, installed from PyTorch's own CPU wheel index BEFORE
# sentence-transformers -- the default PyPI `torch` wheel bundles the
# full CUDA/NVIDIA runtime regardless of whether a GPU is present
# (Technical Debt #19, PROJECT_STATE.md section 12): confirmed in a
# Phase 5 Milestone 1 sandbox install to add ~5.8GB, ~3.2GB of it pure
# CUDA, for an adapter (SentenceTransformerEmbeddingPort) that is
# CPU-only by design and never touches a GPU. Installing the CPU wheel
# first satisfies sentence-transformers' own torch dependency before
# pip ever gets a chance to resolve the CUDA-bundled one. NOTE: this
# exact install step could not be executed/verified in this milestone's
# sandbox -- download.pytorch.org is outside that sandbox's network
# allowlist (the same limitation already recorded against TD #19) -- so
# this is the standard, documented PyTorch CPU-install technique,
# reproducible in a real Docker build, not one confirmed working here.
RUN if [ "$INSTALL_RAG_DEPENDENCIES" = "true" ]; then \
        pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
        pip install --no-cache-dir ".[rag]"; \
    else \
        pip install --no-cache-dir .; \
    fi

# Scanner binaries (`nuclei`, `nmap`) -- the real executables the two wired
# ActiveScanner adapters (app/scanner_engine/adapters/{nuclei,nmap}/
# adapter.py) launch via run_scanner_subprocess. Without them the
# `worker` container's EXECUTE_SCANNER step always fails: VALIDATE_TARGET
# passes, then asyncio.create_subprocess_exec raises FileNotFoundError
# for `nuclei` (recorded on the step's error_message, scan -> FAILED) and
# every later step stays PENDING. The adapters' tests fake the scanner,
# so nothing before this ever ran a real binary.
#
# Same opt-in shape as INSTALL_RAG_DEPENDENCIES above: "true" for the
# `worker` service only (docker-compose.yml), default "false" so the
# `backend`/api image -- which never executes a scanner -- stays free of
# them.
#
# nuclei is fetched in this throwaway stage (curl/unzip never reach the
# runtime image) from the project's official GitHub release, pinned to an
# exact version and verified against that release's published SHA-256
# (<release>/nuclei_<version>_checksums.txt) before it is extracted. To
# bump: change NUCLEI_VERSION and both checksums together. nmap comes from
# the distro package in the runtime stage below.
FROM python:3.12-slim AS scanner-binaries

ARG INSTALL_SCANNER_BINARIES=false
ARG NUCLEI_VERSION=3.11.1
ARG NUCLEI_SHA256_AMD64=ea63d4ae232808cd7c6bc00d0142428e231fab59dae01042246097d195835ab6
ARG NUCLEI_SHA256_ARM64=8044e3d9768ba0a744b2872c1a87e813006f013da97ca9f50f7661a4203bec07

# Always creates /out/bin (empty unless the arg is "true") so the runtime
# stage's COPY --from below has a source in both build modes -- a COPY
# cannot itself be made conditional on a build arg.
RUN mkdir -p /out/bin
RUN if [ "$INSTALL_SCANNER_BINARIES" = "true" ]; then \
        set -eux; \
        apt-get update; \
        apt-get install -y --no-install-recommends curl unzip; \
        arch="$(dpkg --print-architecture)"; \
        case "$arch" in \
            amd64) sha="$NUCLEI_SHA256_AMD64" ;; \
            arm64) sha="$NUCLEI_SHA256_ARM64" ;; \
            *) echo "no pinned nuclei checksum for architecture: $arch" >&2; exit 1 ;; \
        esac; \
        curl -fsSL -o /tmp/nuclei.zip \
            "https://github.com/projectdiscovery/nuclei/releases/download/v${NUCLEI_VERSION}/nuclei_${NUCLEI_VERSION}_linux_${arch}.zip"; \
        echo "${sha}  /tmp/nuclei.zip" | sha256sum -c -; \
        unzip -q /tmp/nuclei.zip nuclei -d /out/bin; \
        chmod 0755 /out/bin/nuclei; \
    fi

FROM python:3.12-slim AS runtime

# Re-declared: a build ARG is scoped to the stage that declares it.
ARG INSTALL_SCANNER_BINARIES=false

# A non-root user to run as -- the same "never run as root" posture
# already enforced in-process for scanner subprocess execution
# (app/scanner_engine/base_scanner.py's NonRootExecutionError guard),
# applied here at the container level too: belt-and-suspenders, not a
# substitute for that in-process check, which still applies regardless
# of what the container's own UID happens to be.
RUN groupadd --system app && useradd --system --gid app --create-home app

# nmap (distro package) plus the pinned nuclei binary from the
# `scanner-binaries` stage above -- see that stage's comment for why.
# Installed here, as root, before the `USER app` line below; both
# binaries are then run by the non-root `app` user (nmap with -sT/-Pn
# only, which needs no raw-socket privileges -- see NmapAdapter). The
# COPY is a no-op (empty directory) when INSTALL_SCANNER_BINARIES is not
# "true".
RUN if [ "$INSTALL_SCANNER_BINARIES" = "true" ]; then \
        apt-get update && \
        apt-get install -y --no-install-recommends nmap && \
        rm -rf /var/lib/apt/lists/*; \
    fi
COPY --from=scanner-binaries /out/bin/ /usr/local/bin/

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
