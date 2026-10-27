#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR/.."
if [ "$#" -lt 1 ]; then
    echo 'Usage: bash scripts/test-login-mac.sh your-email@gmail.com [--headless]' >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo 'Install Python 3 from https://www.python.org/downloads/macos/ and rerun this script.' >&2
    exit 1
fi
python3 -m venv .venv
.venv/bin/python -m pip install 'playwright==1.58.0' 'python-dotenv==1.2.1'
.venv/bin/python -m playwright install chromium
DRAGONKIND_TEST_ACCOUNT=$1
shift
exec .venv/bin/python -m dragonbot desktop-login --account "$DRAGONKIND_TEST_ACCOUNT" "$@"
