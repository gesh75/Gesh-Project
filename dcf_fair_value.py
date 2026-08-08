"""Shim — canonical implementation is ``gesh_core.dcf.fair_value`` (v0.8.0).

The 2026-08-07 relocation (#221) copied the pre-review DCF here so the publisher
repo could reach it; gesh-core's extraction superseded that the next day with the
practitioner-reviewed methodology (operating NWC, Blume beta + fragility flag,
average-ratio base, sector/margin guards). This shim keeps the flat-layout import
working while guaranteeing product and engine value against ONE methodology —
a drifting local copy here would quietly publish DO-NOT-PUBLISH numbers.
"""

import sys

from gesh_core.dcf import fair_value as _impl

sys.modules[__name__] = _impl
