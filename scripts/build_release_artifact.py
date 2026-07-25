#!/usr/bin/env python3
"""CLI entrypoint for the deterministic GESH release builder."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    runpy.run_module("release_artifact", run_name="__main__")
