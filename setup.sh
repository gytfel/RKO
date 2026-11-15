#!/usr/bin/env bash
# Backwards compatible: one installer instead of manual Docker setup.
set -euo pipefail
cd "$(dirname "$0")"
exec bash ./install.sh "$@"
