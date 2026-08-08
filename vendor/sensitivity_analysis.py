"""Shim — vendored file's canonical home is gesh_core.dcf.vendor (see NOTICE.md)."""

import sys

from gesh_core.dcf.vendor import sensitivity_analysis as _impl

sys.modules[__name__] = _impl
