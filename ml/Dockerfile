# Image of the ML jobs the control plane starts (build_features, evaluate, train).
# Build context is the repository root (the proto sources live in ./proto):
#   docker build -f ml/Dockerfile -t corvus-ml .
# Run (the control plane's runner sets the environment, see docs/reference/ml-jobs.md):
#   docker run --rm -e CORVUS_JOB_ID=... corvus-ml train
FROM ghcr.io/astral-sh/uv:0.12.24 AS uv

FROM python:3.15-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
# The venv is created where it runs: console scripts carry the interpreter path of the build.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build/ml
COPY ml/pyproject.toml ml/uv.lock ml/README.md ./
# Dependencies first, for layer caching: the eval extra (CPU-only torch from the pytorch-cpu index
# pinned in uv.lock, scikit-learn, ONNX, ONNX Runtime) plus protoc (proto group, build time only).
RUN uv sync --frozen --no-dev --extra eval --group proto --no-install-project
COPY proto /build/proto
COPY ml/scripts scripts
COPY ml/src src
# Generate the protobuf bindings, then install the project without the proto group: uv removes
# grpcio-tools again, so the runtime has no protoc.
RUN PYTHON=/opt/venv/bin/python scripts/gen_proto.sh \
 && uv sync --frozen --no-dev --extra eval --no-editable

FROM python:3.15-slim
RUN groupadd --system --gid 10001 corvus \
 && useradd --system --uid 10001 --gid corvus --no-create-home corvus
COPY --from=build /opt/venv /opt/venv
# HOME must be writable for the non-root user (ONNX Runtime writes a telemetry id there).
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp
USER 10001:10001
# Temporary files (feature builds, exports) go to the system temp dir; point
# CORVUS_JOB_SCRATCH_DIR at a mounted volume for large snapshots.
ENTRYPOINT ["corvus-job"]
