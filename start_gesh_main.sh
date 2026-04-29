#!/bin/bash
# GESH Main Server — port 9000
ulimit -n 65536
cd /Users/georgigaydarov/Projects/gesh-project
source venv/bin/activate
exec python3 server.py
