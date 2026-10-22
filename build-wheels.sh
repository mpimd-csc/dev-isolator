#!/usr/bin/env bash
#
# build-wheels.sh - Build the Python source distribution (sdist) and wheel
#                   for the dev-isolator package.
#
# Usage:
#   ./build-wheels.sh
#
# Produces the source package (.tar.gz) and the wheel (.whl) in the
# `dist/` directory, as configured by pyproject.toml.

set -euo pipefail

# Resolve the directory containing this script so the build works no
# matter where it is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PY="${PYTHON:-python3}"

echo "==> Using interpreter: $("$PY" --version)"

# The standard 'build' module produces both the sdist and the wheel in a
# clean, isolated environment. Make sure it is available first.
if ! "$PY" -c "import build" >/dev/null 2>&1; then
    echo "==> Installing the 'build' module ..."
    "$PY" -m pip install --upgrade build
fi

echo "==> Building source distribution and wheel ..."
# --outdir : place artifacts in dist/
# --sdist  : build the source package (.tar.gz)
# --wheel  : build the wheel (.whl)
# Build isolation is the default: the backend and its dependencies are
# installed into a fresh environment (PEP 517), so the build does not
# depend on what is installed in the current interpreter.
"$PY" -m build \
    --outdir "$SCRIPT_DIR/dist" \
    --sdist \
    --wheel

echo
echo "==> Build finished. Artifacts in: $SCRIPT_DIR/dist"
ls -l "$SCRIPT_DIR/dist"
