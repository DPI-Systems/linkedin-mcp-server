#!/usr/bin/env bash
# From the Mac: copy the working tree (no venvs, no CRM, no local data) to
# dpi-lab. Usage: dpi_deploy/sync.sh [dpi@100.81.155.12]
set -euo pipefail
host="${1:-dpi@100.81.155.12}"
cd "$(dirname "$0")/.."
rsync -az --delete \
  --exclude .git --exclude .venv --exclude dpi_crm --exclude node_modules \
  --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache \
  ./ "$host:/opt/dpi/linkedin-mcp/src/"
