"""Fire-and-forget background tasks.

``asyncio.create_task`` only keeps a *weak* reference to the task, so a task
with no other live reference can be garbage-collected before it finishes.
``spawn`` keeps a strong reference in a module-level set until the task is
done, then drops it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

_background: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Schedule ``coro`` detached from the caller, holding a strong reference
    so it is not garbage-collected mid-flight."""
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task
