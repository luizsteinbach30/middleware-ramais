"""Common FastAPI dependencies."""

from __future__ import annotations

from middleware_monitor.core.db import get_session  # re-export
from middleware_monitor.core.security import (  # re-export
    get_current_user,
    require_admin,
    require_csrf,
)

__all__ = ["get_current_user", "get_session", "require_admin", "require_csrf"]
