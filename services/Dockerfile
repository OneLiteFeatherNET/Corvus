# Build context is the repository root (the proto sources live in ./proto):
#   docker build -f services/Dockerfile -t corvus-services .
FROM ghcr.io/astral-sh/uv:0.12.24 AS uv

FROM python:3.13-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
# The venv is built at the path it runs from: console-script shebangs and the pyvenv.cfg
# point into it, so copying a venv built elsewhere would break corvus-ingest and friends.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
# corvus-ml is a uv path dependency (../ml, serve extra only: onnxruntime, no torch/scikit-learn).
COPY ml/pyproject.toml ml/README.md /build/ml/
COPY ml/src /build/ml/src
WORKDIR /build/services
COPY services/pyproject.toml services/uv.lock services/README.md ./
# Dependencies (incl. corvus-ml) plus protoc (proto group) first, for layer caching.
RUN uv sync --frozen --no-dev --group proto --no-install-project
COPY proto /build/proto
COPY services/scripts scripts
COPY services/src src
# Generate the protobuf bindings at build time, then install the project without the proto group.
RUN PATH="/opt/venv/bin:$PATH" scripts/gen_proto.sh \
 && uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim
RUN groupadd --system --gid 10001 corvus \
 && useradd --system --uid 10001 --gid corvus --no-create-home corvus
COPY --from=build /opt/venv /opt/venv
COPY services/migrations /app/migrations
ENV PATH="/opt/venv/bin:$PATH" \
    CORVUS_MIGRATIONS_DIR=/app/migrations \
    PYTHONUNBUFFERED=1
USER 10001:10001
# corvus-ingest and corvus-score export metrics, /livez (liveness) and /healthz (readiness) on 9464; corvus-score also needs a
# writable model cache (CORVUS_SCORE_MODELS_DIR, default /tmp/corvus-score/models).
EXPOSE 9464
CMD ["corvus-ingest"]
