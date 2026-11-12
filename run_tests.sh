#!/usr/bin/env bash
# 运行全部测试。优先使用仓库内 venv（含官方 protobuf / grpcio-tools），
# 否则回退到当前 python（需自行装好测试依赖）。
set -euo pipefail
cd "$(dirname "$0")"

if [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
else
  PY=python3
fi

exec "$PY" -I -m unittest discover -s tests -t tests -v
