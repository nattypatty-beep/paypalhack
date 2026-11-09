#!/usr/bin/env bash
# MandatePay - one-command local start for macOS / Linux.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating .venv ..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --disable-pip-version-check -q --upgrade pip
python -m pip install --disable-pip-version-check -q -r requirements.txt

echo
echo "MandatePay is starting at http://127.0.0.1:8000"
echo "Mock PayPal + rules engines are used unless you add credentials to .env"
echo "Press Ctrl+C to stop."
echo

exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
