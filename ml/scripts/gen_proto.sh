#!/usr/bin/env bash
# Generates the Python protobuf bindings for the capture schema into
# src/corvus_ml/_gen/ (gitignored). Run from anywhere: ml/scripts/gen_proto.sh
#
# Build-time step: needs protoc from the `proto` dependency group (grpcio-tools), which is not part
# of the runtime install. In a checkout corvus_ml.proto also generates the bindings on first
# import when they are missing or older than the .proto; images run this script instead.
# PYTHON selects the interpreter that has grpcio-tools (default: `uv run python`).
set -euo pipefail

ml_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
proto_dir="${CORVUS_PROTO_DIR:-${ml_dir}/../proto}"
out_dir="${ml_dir}/src/corvus_ml/_gen"

rm -rf "${out_dir}"
mkdir -p "${out_dir}"
cd "${ml_dir}"
${PYTHON:-uv run python} -m grpc_tools.protoc \
  --proto_path="${proto_dir}" \
  --python_out="${out_dir}" \
  "${proto_dir}/corvus/capture/v1/capture.proto"
echo "generated into ${out_dir}"
