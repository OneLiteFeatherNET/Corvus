#!/usr/bin/env bash
# Generate the capture protobuf bindings into src/corvus_services/_gen (gitignored).
# Needs the "proto" dependency group (grpcio-tools): uv run --group proto scripts/gen_proto.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
proto_root="${CORVUS_PROTO_DIR:-$here/../proto}"
out="$here/src/corvus_services/_gen"

rm -rf "$out"
mkdir -p "$out"
python -m grpc_tools.protoc \
  --proto_path="$proto_root" \
  --python_out="$out" \
  "$proto_root/corvus/capture/v1/capture.proto"
echo "generated $out/corvus/capture/v1/capture_pb2.py"
