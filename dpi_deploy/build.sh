#!/usr/bin/env bash
# Build on dpi-lab, from the source synced to /opt/dpi/linkedin-mcp/src.
#   1. upstream's Dockerfile, untouched -> dpi/linkedin-mcp-base:local
#   2. dpi_deploy/Dockerfile on top     -> dpi/linkedin-mcp:local
set -euo pipefail
cd "$(dirname "$0")/.."
docker build -t dpi/linkedin-mcp-base:local -f Dockerfile .
docker build -t dpi/linkedin-mcp:local -f dpi_deploy/Dockerfile .
