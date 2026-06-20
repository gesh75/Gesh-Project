#!/bin/bash
# GESH Main Server — port 9000
ulimit -n 65536
cd "$(dirname "$0")"
# Tracker DB (wash_sale_log etc.) lives in the engine repo. gesh_core.tax_harvester
# resolves it via GESH_TRACKER_DB; default to the sibling engine repo's db so the
# /api/tax-harvest endpoint reads the same db as before the gesh-core extraction.
export GESH_TRACKER_DB="${GESH_TRACKER_DB:-$(dirname "$(pwd)")/gesh-project-trading/gesh_financial.db}"
source venv/bin/activate
exec python3 server.py
