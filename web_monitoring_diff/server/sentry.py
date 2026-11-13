from concurrent.futures import Future, ProcessPoolExecutor
from functools import partial
import sentry_sdk
from sentry_sdk.integrations import Integration
from typing import Any, Callable


class SentryProcessPoolExecutorIntegration(Integration):
    """
    Adds Sentry tracing support to ``concurrent.futures.ProcessPoolExecutor``.

    Calls to `start_span()` from within a task any ``ProcessPoolExecutor`` is
    running will create a span that is linked to the span/transaction that was
    active when the task was submitted to the executor, so they form one
    continuous trace when viewed in Sentry.
    """

    identifier = "ProcessPoolExecutor"

    @staticmethod
    def setup_once() -> None:
        process_pool_submit = ProcessPoolExecutor.submit

        def sentry_submit(self, fn: Callable, /, *args, **kwargs) -> Future[Any]:
            """
            Wraps ``ProcessPoolExecutor.submit`` in order to pass distributed
            trace information down to the subprocess.
            """
            bound_fn = partial(fn, *args, **kwargs)
            trace = {
                'sentry-trace': sentry_sdk.get_traceparent(),
                'baggage': sentry_sdk.get_baggage(),
            }
            return process_pool_submit(
                self,
                SentryProcessPoolExecutorIntegration.task_wrapper,
                bound_fn,
                trace,
            )

        ProcessPoolExecutor.submit = sentry_submit

    @staticmethod
    def task_wrapper(fn: Callable, trace: dict[str, Any]) -> Any:
        """
        Wraps a task submitted to an executor. Picks up distributed trace info.
        """
        transaction = sentry_sdk.continue_trace(trace, op='executor.run', name='executor.run')
        with sentry_sdk.start_transaction(transaction):
            return fn()
