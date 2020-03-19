import hashlib
import io
import logging
import lxml.html
import os
import queue
import re
import requests
import requests.adapters
import signal
import threading
import time


logger = logging.getLogger(__name__)

WHITESPACE_PATTERN = re.compile(r'\s+')


def extract_title(content_bytes, encoding='utf-8'):
    "Return content of <title> tag as string. On failure return empty string."
    content_str = content_bytes.decode(encoding=encoding, errors='ignore')
    # The parser expects a file-like, so we mock one.
    content_as_file = io.StringIO(content_str)
    try:
        title = lxml.html.parse(content_as_file).find(".//title")
    except Exception:
        return ''

    if title is None or title.text is None:
        return ''

    # In HTML, all consecutive whitespace (including line breaks) collapses
    return WHITESPACE_PATTERN.sub(' ', title.text.strip())


def hash_content(content_bytes):
    "Create a version_hash for the content of a snapshot."
    return hashlib.sha256(content_bytes).hexdigest()


class RateLimit:
    """
    RateLimit is a simple locking mechanism that can be used to enforce rate
    limits and is safe to use across multiple threads. It can also be used as
    a context manager.

    Calling `rate_limit_instance.wait()` blocks until a minimum time has passed
    since the last call. Using `with rate_limit_instance:` blocks entries to
    the context until a minimum time since the last context entry.

    Parameters
    ----------
    per_second : int or float
        The maximum number of calls per second that are allowed. If 0, a call
        to `wait()` will never block.

    Examples
    --------
    Slow down a tight loop to only occur twice per second:

    >>> limit = RateLimit(per_second=2)
    >>> for x in range(10):
    >>>     with limit:
    >>>         print(x)
    """
    def __init__(self, per_second=10):
        self._lock = threading.RLock()
        self._last_call_time = 0
        if per_second <= 0:
            self._minimum_wait = 0
        else:
            self._minimum_wait = 1.0 / per_second

    def wait(self):
        if self._minimum_wait == 0:
            return

        with self._lock:
            current_time = time.time()
            idle_time = current_time - self._last_call_time
            if idle_time < self._minimum_wait:
                time.sleep(self._minimum_wait - idle_time)

            self._last_call_time = time.time()

    def __enter__(self):
        self.wait()

    def __exit__(self, type, value, traceback):
        pass


def get_color_palette():
    """
    Read and return the CSS color env variables that indicate the colors in
    html_diff_render, differs and links_diff.

    Returns
    ------
    palette: Dictionary
        A dictionary containing the differ_insertion and differ_deletion css
        color codes
    """
    differ_insertion = os.environ.get('DIFFER_COLOR_INSERTION', '#a1d76a')
    differ_deletion = os.environ.get('DIFFER_COLOR_DELETION', '#e8a4c8')
    return {'differ_insertion': differ_insertion,
            'differ_deletion': differ_deletion}


def iterate_into_queue(queue, iterable):
    """
    Read items from an iterable and place them onto a FiniteQueue.

    Parameters
    ----------
    queue: FiniteQueue
    iterable: sequence
    """
    for item in iterable:
        queue.put(item)
    queue.end()


class FiniteQueue(queue.SimpleQueue):
    """
    A queue that is iterable, with a defined end.

    The end of the queue is indicated by the `FiniteQueue.QUEUE_END` object.
    If you are using the iterator interface, you won't ever encounter it, but
    if reading the queue with `queue.get`, you will receive
    `FiniteQueue.QUEUE_END` if you’ve reached the end.
    """

    # Use a class instad of `object()` for more readable names for debugging.
    class QUEUE_END:
        ...

    def __init__(self):
        super().__init__()
        self._ended = False
        # The Queue documentation suggests that put/get calls can be
        # re-entrant, so we need to use RLock here.
        self._lock = threading.RLock()

    def end(self):
        self.put(self.QUEUE_END)

    def get(self, *args, **kwargs):
        with self._lock:
            if self._ended:
                return self.QUEUE_END
            else:
                value = super().get(*args, **kwargs)
                if value is self.QUEUE_END:
                    self._ended = True

                return value

    def __iter__(self):
        return self

    def __next__(self, timeout=None):
        item = self.get()
        if item is self.QUEUE_END:
            raise StopIteration

        return item

    def iterate_with_timeout(self, timeout):
        while True:
            try:
                yield self.__next__(timeout)
            except StopIteration:
                return


class DepthCountedContext:
    """
    DepthCountedContext is a mixin or base class for context managers that need
    to be perform special operations only when all nested contexts they might
    be used in have exited.

    Override the `__exit_all__(self, type, value, traceback)` method to get a
    version of `__exit__` that is only called when exiting the top context.

    As a convenience, the built-in `__enter__` returns `self`, which is fairly
    common, so in many cases you don't need to author your own `__enter__` or
    `__exit__` methods.
    """
    _context_depth = 0

    def __enter__(self):
        self._context_depth += 1
        return self

    def __exit__(self, type, value, traceback):
        if self._context_depth > 0:
            self._context_depth -= 1
        if self._context_depth == 0:
            return self.__exit_all__(type, value, traceback)

    def __exit_all__(self, type, value, traceback):
        """
        A version of the normal `__exit__` context manager method that only
        gets called when the top level context is exited. This is meant to be
        overridden in your class.
        """
        pass


class SessionClosedError(Exception):
    ...


class DisableAfterCloseSession(requests.Session):
    """
    A custom session object raises a :class:`SessionClosedError` if you try to
    use it after closing it, to help identify and avoid potentially dangerous
    code patterns. (Standard session objects continue to be usable after
    closing, even if they may not work exactly as expected.)
    """
    _closed = False

    def close(self, disable=True):
        super().close()
        if disable:
            self._closed = True

    def send(self, *args, **kwargs):
        if self._closed:
            raise SessionClosedError('This session has already been closed '
                                     'and cannot send new HTTP requests.')

        return super().send(*args, **kwargs)


class Signal:
    """
    A context manager to handle signals from the system safely. It keeps track
    of previous signal handlers and ensures that they are put back into place
    when the context exits.

    Parameters
    ----------
    signals : int or tuple of int
        The signal or list of signals to handle.
    handler : callable
        A signal handler function of the same type used with `signal.signal()`.
        See: https://docs.python.org/3.6/library/signal.html#signal.signal

    Examples
    --------
    Ignore SIGINT (ctrl+c) and print a glib message instead of quitting:

    >>> def ignore_signal(signal_type, frame):
    >>>     print("Sorry, but you can't quit this program that way!")
    >>>
    >>> with Signal((signal.SIGINT, signal.SIGTERM), ignore_signal):
    >>>     do_some_work_that_cant_be_interrupted()
    """
    def __init__(self, signals, handler):
        self.handler = handler
        self.old_handlers = {}
        try:
            self.signals = tuple(signals)
        except TypeError:
            self.signals = (signals,)

    def __enter__(self):
        for signal_type in self.signals:
            self.old_handlers[signal_type] = signal.getsignal(signal_type)
            signal.signal(signal_type, self.handler)

        return self

    def __exit__(self, type, value, traceback):
        for signal_type in self.signals:
            signal.signal(signal_type, self.old_handlers[signal_type])


class QuitSignal(Signal):
    """
    A context manager that handles system signals by triggering a
    `threading.Event` instance, giving your program an opportunity to clean up
    and shut down gracefully. If the signal is repeated a second time, the
    process quits immediately.

    Parameters
    ----------
    signals : int or tuple of int
        The signal or list of signals to handle.
    graceful_message : string, optional
        A message to print to stdout when a signal is received.
    final_message : string, optional
        A message to print to stdout before exiting the process when a repeat
        signal is received.

    Examples
    --------
    Quit on SIGINT (ctrl+c) or SIGTERM:

    >>> with QuitSignal((signal.SIGINT, signal.SIGTERM)) as cancel:
    >>>     for item in some_list:
    >>>         if cancel.is_set():
    >>>             break
    >>>         do_some_work()
    """
    def __init__(self, signals, graceful_message=None, final_message=None):
        self.event = threading.Event()
        self.graceful_message = graceful_message or (
            'Attempting to finish existing work before exiting. Press ctrl+c '
            'to stop immediately.')
        self.final_message = final_message or (
            'Stopping immediately and aborting all work!')
        super().__init__(signals, self.handle_interrupt)

    def handle_interrupt(self, signal_type, frame):
        if not self.event.is_set():
            print(self.graceful_message)
            self.event.set()
        else:
            print(self.final_message)
            os._exit(100)

    def __enter__(self):
        super().__enter__()
        return self.event
