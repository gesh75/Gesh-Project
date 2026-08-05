# syntax=docker/dockerfile:1.6
# GESH Financial — Port 9000 product image (minimal)
#
# Private dependency: gesh-core requires a GitHub PAT at build time.
#
#   GESH_CORE_PAT=ghp_... docker build --secret id=gesh_core_pat,env=GESH_CORE_PAT -t gesh-main:local .
#   docker run --rm -p 9000:9000 --env-file .env gesh-main:local
#
# server.py binds 127.0.0.1 by default. Only publish the port behind a proxy
# you trust — never expose the raw container on the public internet.

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock ./

RUN --mount=type=secret,id=gesh_core_pat,required=true \
    set -eu; \
    export GESH_CORE_PAT="$(cat /run/secrets/gesh_core_pat)"; \
    if [ -z "${GESH_CORE_PAT}" ]; then \
      echo "gesh_core_pat BuildKit secret required to install private gesh-core" >&2; \
      exit 1; \
    fi; \
    CORE_SPEC="$(grep '^gesh-core @' requirements.lock)"; \
    case "$CORE_SPEC" in \
      "gesh-core @ git+https://github.com/gesh75/gesh-core.git@"*) ;; \
      *) echo "unexpected gesh-core source: ${CORE_SPEC}" >&2; exit 1 ;; \
    esac; \
    AUTH_HEADER="$(python -c 'import base64, os; print("AUTHORIZATION: basic " + base64.b64encode(("x-access-token:" + os.environ["GESH_CORE_PAT"]).encode()).decode())')"; \
    git config --global http.https://github.com/gesh75/gesh-core.git.extraheader "${AUTH_HEADER}"; \
    pip install --no-cache-dir --upgrade pip; \
    pip install --no-cache-dir "${CORE_SPEC}"; \
    rm -f /root/.gitconfig; \
    grep -v '^gesh-core @' requirements.lock > /tmp/ci-rest.lock; \
    pip install --no-cache-dir -r /tmp/ci-rest.lock; \
    pip check

COPY . .

ENV PORT=9000
EXPOSE 9000

CMD ["python", "server.py"]
