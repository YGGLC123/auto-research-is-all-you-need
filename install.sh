#!/usr/bin/env bash
# auto-research one-click entry (POSIX). Usage: ./install.sh [install|uninstall|status|release]
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$root/scripts/deploy.py" "${1:-install}"
