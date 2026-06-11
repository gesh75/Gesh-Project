#!/bin/bash
# GESH Main Server — port 9000
ulimit -n 65536
cd "$(dirname "$0")"
source venv/bin/activate
exec python3 server.py
