"""Share overlapping read-only probes, without retaining authorization state."""
from __future__ import annotations

import asyncio


class ConcurrentReads:
    def __init__(self):
        self.pending = {}

    async def get(self, key, factory):
        task = self.pending.get(key)
        if task is None or task.done():
            task = asyncio.create_task(factory())
            self.pending[key] = task

            def done(completed):
                if self.pending.get(key) is completed:
                    self.pending.pop(key, None)
                if not completed.cancelled():
                    completed.exception()  # Consume an error even if all waiters left.

            task.add_done_callback(done)
        # A disconnected speculative reader cannot cancel a foreground reader.
        return await asyncio.shield(task)

    def invalidate(self):
        # Existing waiters still finish; subsequent requests start new probes.
        self.pending.clear()
