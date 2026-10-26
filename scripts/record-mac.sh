#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR/.."
if ! command -v python3 >/dev/null 2>&1; then
    echo 'Install Python 3 from https://www.python.org/downloads/macos/ and rerun this script.' >&2
    exit 1
fi
python3 -m venv .venv
.venv/bin/python -m pip install 'playwright==1.58.0'
.venv/bin/python -m playwright install chromium
mkdir -p data
RECORDING_PATH="data/recording-$(date +%Y%m%d-%H%M%S).py"
echo "Recording locally to $RECORDING_PATH"
echo 'Turn recording OFF in Inspector before entering MFA, then ON again after login.'
echo 'Review and redact any verification codes before sharing the recording. Do not run the generated script yet.'
exec .venv/bin/python -m playwright codegen --browser chromium --timeout 300000 --target python --output "$RECORDING_PATH" https://dragonkind.com
