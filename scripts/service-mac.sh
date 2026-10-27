#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR/.."
if [ "$(uname -s)" != Darwin ]; then
    echo 'This launcher requires macOS; use Docker Compose on Windows.' >&2
    exit 1
fi
if [ "${1:-status}" = install ]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install 'playwright==1.58.0' 'python-dotenv==1.2.1'
    .venv/bin/python -m playwright install chromium
elif [ ! -x .venv/bin/python ]; then
    echo 'Install first: bash scripts/service-mac.sh install' >&2
    exit 1
fi
exec .venv/bin/python -m dragonbot.macos_service "$@"
