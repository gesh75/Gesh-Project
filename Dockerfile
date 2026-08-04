# GESH Financial — Port 9000 product image (minimal)
#
# Private dependency: gesh-core requires a GitHub PAT at build time.
#
#   docker build --build-arg GESH_CORE_PAT=ghp_... -t gesh-main:local .
#   docker run --rm -p 9000:9000 --env-file .env gesh-main:local
#
# server.py binds 127.0.0.1 by default. Only publish the port behind a proxy
# you trust — never expose the raw container on the public internet.

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ARG GESH_CORE_PAT
COPY requirements.lock ./

RUN set -euo pipefail; \
    if [ -z "${GESH_CORE_PAT:-}" ]; then \
      echo "GESH_CORE_PAT build-arg required to install private gesh-core" >&2; \
      exit 1; \
    fi; \
    CORE_SPEC="$(grep '^gesh-core @' requirements.lock)"; \
    case "$CORE_SPEC" in \
      "gesh-core @ git+https://github.com/gesh75/gesh-core.git@"*) ;; \
      *) echo "unexpected gesh-core source: ${CORE_SPEC}" >&2; exit 1 ;; \
    esac; \
    CORE_SHA="${CORE_SPEC##*@}"; \
    pip install --no-cache-dir --upgrade pip; \
    pip install --no-cache-dir \
      "gesh-core @ git+https://x-access-token:${GESH_CORE_PAT}@github.com/gesh75/gesh-core.git@${CORE_SHA}"; \
    grep -v '^gesh-core @' requirements.lock > /tmp/ci-rest.lock; \
    pip install --no-cache-dir -r /tmp/ci-rest.lock; \
    pip check

COPY . .

ENV PORT=9000
EXPOSE 9000

CMD ["python", "server.py"]
