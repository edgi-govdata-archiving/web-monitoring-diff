import asyncio
import concurrent.futures
import logging
import functools
from ..utils import shutdown_executor_in_loop

logger = logging.getLogger(__name__)

class DiffPoolError(Exception):
    """Raised when the process pool is unrecoverable and requires a server shutdown."""
    pass

class DiffExecutorManager:
    """
    Manages a pool of worker processes. Handles worker recycling based on 
    usage counts and recovers from process crashes.
    """
    def __init__(self, parallelism, max_diffs, initializer, restart_on_fail=True):
        self.parallelism = parallelism
        self.max_diffs_per_worker = max_diffs
        self.initializer = initializer
        self.restart_on_fail = restart_on_fail
        
        self.executor = None
        self.remaining_diffs = 0
        self.terminating = False
        self._lock = asyncio.Lock()

    async def _reset_executor(self):
        """Internal method to shut down the old pool and start a fresh one."""
        if self.executor:
            try:
                await shutdown_executor_in_loop(self.executor)
            except Exception:
                pass
        
        self.executor = concurrent.futures.ProcessPoolExecutor(
            self.parallelism,
            initializer=self.initializer)
        
        # Refill the tally book: Total jobs allowed for the whole pool
        self.remaining_diffs = self.max_diffs_per_worker * self.parallelism

    async def get_executor(self, force_reset=False):
        """Returns the current executor. Resets it if forced or usage limit hit."""
        if self.terminating:
            raise RuntimeError('Diff executor is being shut down.')

        async with self._lock:
            # Check if we need to recycle the pool
            limit_hit = self.max_diffs_per_worker > 0 and self.remaining_diffs <= 0
            
            if force_reset or not self.executor or limit_hit:
                await self._reset_executor()
            
            return self.executor

    async def run_diff(self, caller_func, func, a, b, params, tries=2):
        """
        Runs a diff in the process pool. 
        Handles the usage counter and retries on process failure.
        """
        if self.max_diffs_per_worker > 0:
            async with self._lock:
                self.remaining_diffs -= 1

        force_reset = False
        for attempt in range(tries):
            # Fetch the executor (resets automatically if force_reset is True)
            executor = await self.get_executor(force_reset=force_reset)
            
            try:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(
                    executor, functools.partial(caller_func, func, a, b, **params))
            
            except concurrent.futures.process.BrokenProcessPool:
                if attempt + 1 < tries:
                    logger.warning("Process pool broken; signaling reset for retry...")
                    force_reset = True 
                else:
                    if not self.restart_on_fail:
                        logger.error('Process pool failed; triggering server quit...')
                        raise DiffPoolError("Unrecoverable process pool failure")
                    raise

    async def shutdown(self, immediate=False):
        """Gracefully or immediately shut down all worker processes."""
        self.terminating = True
        if self.executor:
            if immediate:
                for child in self.executor._processes.values():
                    child.kill()
            else:
                await shutdown_executor_in_loop(self.executor)