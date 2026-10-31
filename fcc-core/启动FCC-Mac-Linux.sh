#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo 'Install Python 3.11+ first.'; exit 1; }
if [ ! -x '.venv/bin/python' ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -e .
echo 'FCC AI Bootstrap: http://127.0.0.1:8099/start'
echo 'FCC Guided setup: http://127.0.0.1:8099/setup'
echo 'FCC Easy I/O: http://127.0.0.1:8099/easy'
echo 'FCC advanced UI: http://127.0.0.1:8099/expert'
echo 'Stop with Ctrl+C.'
.venv/bin/python -m fcc.server --host 127.0.0.1 --port 8099 --db fcc.db
