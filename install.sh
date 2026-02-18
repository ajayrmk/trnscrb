#!/usr/bin/env bash
# Bootstrap installer for inscry.
# Run this once:  bash install.sh
set -e

echo ""
echo "Installing inscry..."
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "Python 3 not found. Install from https://python.org and re-run."
    exit 1
fi

PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
PY_VERSION="${PY_MAJOR}.${PY_MINOR}"

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]); then
    echo "Python 3.11+ required (found $PY_VERSION). Install from https://python.org"
    exit 1
fi
echo "  Python $PY_VERSION  ✓"

# Ensure uv is available (preferred tool manager — no sudo needed)
if ! command -v uv &>/dev/null; then
    echo "  Installing uv (package manager)..."
    if command -v brew &>/dev/null; then
        brew install uv
    else
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.cargo/bin:$PATH"
    fi
fi
echo "  uv $(uv --version | cut -d' ' -f2)  ✓"

# Install inscry via uv tool
if [ -f "pyproject.toml" ]; then
    echo "  Installing inscry from source..."
    uv tool install . --force-reinstall 2>/dev/null || uv tool install .
else
    echo "  Installing inscry from PyPI..."
    uv tool install inscry
fi
echo "  inscry installed  ✓"

# Ensure uv tool bin dir is on PATH for this session
export PATH="$HOME/.local/bin:$PATH"

echo ""

# Run the smart interactive installer
inscry install
