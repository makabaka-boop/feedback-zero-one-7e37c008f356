#!/usr/bin/env bash
# 用 grpc_tools.protoc 重新生成描述符集与官方 Python 桩（仅测试需要）。
set -euo pipefail
cd "$(dirname "$0")"

if [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
else
  PY=python3
fi

PROTO_DIR=fixtures/proto
DESC_DIR=fixtures/desc
GEN_DIR=build/gen
mkdir -p "$DESC_DIR" "$GEN_DIR"

for proto in "$PROTO_DIR"/*.proto; do
  name="$(basename "$proto" .proto)"
  "$PY" -m grpc_tools.protoc -I"$PROTO_DIR" \
    --include_imports \
    --descriptor_set_out="$DESC_DIR/$name.bin" \
    "$proto"
done

"$PY" -m grpc_tools.protoc -I"$PROTO_DIR" \
  --python_out="$GEN_DIR" "$PROTO_DIR/telemetry.proto"

echo "描述符与桩代码已重新生成到 $DESC_DIR 与 $GEN_DIR"
