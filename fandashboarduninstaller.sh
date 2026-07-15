#!/bin/bash
# Backwards-compatible entry point. This wrapper never touches nginx or /var/www.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/uninstall.sh" "$@"
