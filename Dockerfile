# syntax=docker/dockerfile:1

# Frontend build stage (DD-5): web/ is compiled to static assets in its own stage and the
# resulting web/dist is copied into the runtime stage below, where FastAPI serves it
# directly (FR-P1: single container, single process; see app.py's static-serving block).
FROM node:22-slim AS frontend-builder

WORKDIR /app/web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

FROM python:3.13-slim AS builder

RUN pip install --no-cache-dir uv==0.9.18

WORKDIR /app

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# Install exactly the committed lockfile (no re-resolution), dependencies first so the
# layer survives source changes, then the project itself as a regular wheel.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Embedding model stage (DD-32): fetches exactly the three files
# scripts/fetch_model.py also fetches for local development, from the same
# revision-pinned URLs verified against the same sha256 digests -- the two must never
# disagree. `ADD --checksum` verifies the digest itself and fails the build on any
# mismatch, so a corrupted or substituted download fails the build rather than
# shipping. The `resolve/<revision>/` URLs answer with a 302 to a CDN host; BuildKit's
# `ADD` follows the redirect and verifies the checksum after, so the redirect alone is
# not a reason to reach for the `curl` fallback. The destination's trailing slash is
# what makes each URL land as a file inside the directory rather than replacing it, so
# `onnx/model.onnx` arrives as `model.onnx` -- the flat layout `GW_MODEL_DIR` plus
# `GW_EMBEDDING_MODEL` expects. `scratch` needs no base image of its own; `ADD` creates
# the destination directory.
FROM scratch AS model

ADD --checksum=sha256:828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35 \
    https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a/onnx/model.onnx \
    /app/models/bge-small-en-v1.5/
ADD --checksum=sha256:d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66 \
    https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a/tokenizer.json \
    /app/models/bge-small-en-v1.5/
ADD --checksum=sha256:094f8e891b932f2000c92cfc663bac4c62069f5d8af5b5278c4306aef3084750 \
    https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a/config.json \
    /app/models/bge-small-en-v1.5/

FROM python:3.13-slim AS runtime

RUN useradd --create-home --uid 1000 appuser

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    GW_DATA_DIR=/data \
    GW_MODEL_DIR=/app/models \
    HF_HUB_OFFLINE=1

COPY --from=builder /opt/venv /opt/venv
COPY --from=frontend-builder /app/web/dist /app/web/dist
COPY --from=model --chown=appuser:appuser /app/models /app/models

# The licence and the third-party notices travel with the image, because publishing an
# image is redistribution and both FSL-1.1-ALv2 and OFL-1.1 require their terms to
# accompany a copy. Left root-owned and world-readable, which is right for licence files
# and is what a plain COPY before USER gives.
COPY LICENSE THIRD_PARTY_NOTICES.md /app/

WORKDIR /app
RUN mkdir -p /data && chown -R appuser:appuser /data

USER appuser

EXPOSE 8000
VOLUME ["/data"]

# Provenance, last on purpose: GW_REVISION changes on every commit, and every instruction
# after a changed one is a cache miss, so declaring it earlier would rebuild the venv, the
# frontend and the 128 MB model layer for a metadata change.
#
# `unknown` is the default rather than an empty string so a build that forgot the argument
# is distinguishable from one that passed an empty one; container_tests/test_image_notices.py
# rejects both. CI's `image` job passes the commit's SHA, and tests/test_supply_chain.py
# pins that it still does, because the job is skipped on documentation-only changes and
# nothing else would notice the flag going missing.
ARG GW_REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/glosswork/glosswork"
LABEL org.opencontainers.image.revision="$GW_REVISION"
LABEL org.opencontainers.image.licenses="FSL-1.1-ALv2"

ENTRYPOINT ["python", "-m", "glosswork.entrypoint"]
