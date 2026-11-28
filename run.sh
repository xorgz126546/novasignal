#!/usr/bin/env bash
# Local run: builds the dashboard and serves dashboard + API on http://localhost:8000 (single service).
set -euo pipefail
cd "$(dirname "$0")"
[ -f backend/.env ] || cp .env.example backend/.env
( cd frontend && npm install --no-audit --no-fund && npm run build )
cd backend
python3 -m pip install -q -r requirements.txt
STATIC_DIR=../frontend/dist exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
