# syntax=docker/dockerfile:1.7
#
# openPDN production image.
#
#   stage 1  build the frontend with Node
#   stage 2  build a Python virtualenv holding openPDN and its dependencies
#   stage 3  runtime: interpreter + venv + built frontend, nothing else
#
# The backend serves the built frontend, so one image is one deployment unit.
# Splitting them later is a deployment decision and changes only this file and
# the compose stack.

# --- stage 1: frontend ------------------------------------------------------
FROM node:24-alpine AS web-build
WORKDIR /build

# Manifests first: dependency installation is cached until they change.
COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
RUN npm ci

COPY apps/web ./apps/web
RUN npm run build --workspace apps/web


# --- stage 2: backend -------------------------------------------------------
FROM python:3.13-slim AS backend-build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Only what the wheel needs; tests, docs and CI never enter the image.
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps/api ./apps/api
COPY apps/cli ./apps/cli
RUN pip install .


# --- stage 3: runtime -------------------------------------------------------
FROM python:3.13-slim AS runtime

# Build metadata, supplied by CI. Kept as labels rather than baked into code so
# the application version stays the one source of truth.
ARG VCS_REF="unknown"
ARG BUILD_DATE="unknown"
LABEL org.opencontainers.image.title="openPDN" \
      org.opencontainers.image.description="Open-source PCB DC conduction analysis" \
      org.opencontainers.image.source="https://github.com/unearth4334/openPDN" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    OPENPDN_ENVIRONMENT=production \
    OPENPDN_LOG_FORMAT=json \
    OPENPDN_API_HOST=0.0.0.0 \
    OPENPDN_API_PORT=8000 \
    OPENPDN_STATIC_DIR=/srv/openpdn/web \
    OPENPDN_DATA_DIR=/var/lib/openpdn \
    OPENPDN_CACHE_DIR=/var/cache/openpdn

# Unprivileged runtime user; nothing in the image is writable by it except the
# two data locations below.
RUN groupadd --system --gid 10001 openpdn \
 && useradd --system --uid 10001 --gid openpdn --no-create-home openpdn \
 && mkdir -p /var/lib/openpdn /var/cache/openpdn /srv/openpdn \
 && chown -R openpdn:openpdn /var/lib/openpdn /var/cache/openpdn

COPY --from=backend-build /opt/venv /opt/venv
COPY --from=web-build /build/apps/web/dist /srv/openpdn/web

# Persistent data:  /var/lib/openpdn   imported boards and studies
# Regenerable data: /var/cache/openpdn meshes, matrices, cached results
VOLUME ["/var/lib/openpdn", "/var/cache/openpdn"]

USER openpdn
WORKDIR /srv/openpdn
EXPOSE 8000

# The probe uses the interpreter that is already present: no curl, no shell.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3).status==200 else 1)"]

# uvicorn handles SIGTERM, so the container stops cleanly without a shell in
# the way. `openpdn serve` is the same entry point developers use locally.
STOPSIGNAL SIGTERM
ENTRYPOINT ["openpdn"]
CMD ["serve"]
