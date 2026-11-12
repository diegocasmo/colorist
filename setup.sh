#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

brew bundle
mise install
mise exec -- uv sync --locked
