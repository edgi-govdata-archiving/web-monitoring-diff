import asyncio
import concurrent.futures
import logging
import signal

from ..utils import shutdown_executor_in_loop

logger = logging.getLogger(__name__)

def initialize_diff_worker():
    signal.signal(signal.SIGINT, signal.SIG_IGN)

class DiffPool:
    def __init__(self, parallelism, max_diffs_per_worker, restart_broken_differ=False, on_fatal_error=None):
        self.parallelism = parallelism
        self.max_diffs_per_worker = max_diffs_per_worker
        self.restart_broken_differ = restart_broken_differ
        self.on_fatal_error = on_fatal_error

        self.executor = None
        self.remaining_diffs = 0
        self.terminating = False

    def get_executor(self, reset=False):
        if self.terminating:
            raise RuntimeError('Diff executor is being shut down.')

        if reset or not self.executor:
            if self.executor:
                try:
                    shutdown_executor_in_loop(self.executor)
                except Exception:
                    pass
            self.executor = concurrent.futures.ProcessPoolExecutor(
                self.parallelism,
                initializer=initialize_diff_worker)

        return self.executor

    async def diff(self, task_func, tries=2):
        """
        Execute a diff task in the process pool, optionally retrying if the 
        process pool breaks.
        """
        reset = False
        if self.max_diffs_per_worker and self.remaining_diffs <= 0:
            reset = True
            self.remaining_diffs = self.max_diffs_per_worker * self.parallelism
        executor = self.get_executor(reset=reset)

        loop = asyncio.get_running_loop()
        for attempt in range(tries):
            try:
                if self.max_diffs_per_worker:
                    self.remaining_diffs -= 1
                return await loop.run_in_executor(executor, task_func)
            except concurrent.futures.process.BrokenProcessPool:
                if attempt + 1 < tries:
                    old_executor, executor = executor, self.get_executor()
                    if (
                        executor == old_executor or
                        (
                            self.max_diffs_per_worker and
                            self.remaining_diffs <= 0
                        )
                    ):
                        self.remaining_diffs = self.max_diffs_per_worker * self.parallelism
                        executor = self.get_executor(reset=True)
                else:
                    if not self.restart_broken_differ:
                        logger.error('Process pool for diffing has failed too '
                                     'many times; quitting server...')
                        if self.on_fatal_error:
                            self.on_fatal_error()
                    raise

    async def shutdown(self, immediate=False):
        """Stop all child processes used for running diffs."""
        self.terminating = True
        executor = self.executor
        if executor:
            if immediate:
                for child in executor._processes.values():
                    child.kill()
            else:
                await shutdown_executor_in_loop(executor)
