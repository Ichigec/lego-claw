#!/usr/bin/env bash
# Regenerate Python gRPC stubs from `a2a.proto` into ../_generated/.
#
# Run this when:
#   * you bump the vendored proto from upstream a2aproject/A2A;
#   * you add a new RPC/message in `a2a.proto`.
#
# Inside the adapter Dockerfile the same command runs at build time so the
# generated files are baked into the image (no need to commit them — they
# live in `.gitignore` once committed and regenerated). When iterating
# locally, run this script to refresh stubs without rebuilding the image.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROTO_DIR="$SCRIPT_DIR"
OUT_DIR="$SCRIPT_DIR/../_generated"
mkdir -p "$OUT_DIR"

# `grpc_tools.protoc` ships with `grpcio-tools` (pinned alongside
# `grpcio` in the adapter requirements files). Use `python3` for systems
# where only the versioned binary is on PATH (e.g. inside the slim image).
PYTHON_BIN="${PYTHON:-python3}"
"$PYTHON_BIN" -m grpc_tools.protoc \
    -I "$PROTO_DIR" \
    --python_out="$OUT_DIR" \
    --pyi_out="$OUT_DIR" \
    --grpc_python_out="$OUT_DIR" \
    "$PROTO_DIR/a2a.proto"

# grpc_tools.protoc emits absolute-style imports ("import a2a_pb2"); rewrite
# them to be relative to the `_generated` package so Python's import machinery
# does not need `_generated/` on `sys.path`.
GEN_DIR="$OUT_DIR" "$PYTHON_BIN" - <<'PY'
import os
import pathlib
import re

gen_dir = pathlib.Path(os.environ["GEN_DIR"])
for p in gen_dir.glob("*.py"):
    src = p.read_text()
    new = re.sub(r'^import (a2a_pb2)', r'from . import \1', src, flags=re.M)
    if new != src:
        p.write_text(new)
        print(f"patched relative imports in {p.name}")

init = gen_dir / "__init__.py"
if not init.exists():
    init.write_text(
        '"""Auto-generated gRPC stubs for the A2A proto. Do not edit by hand."""\n'
    )
PY

echo "✓ generated stubs in $OUT_DIR"
