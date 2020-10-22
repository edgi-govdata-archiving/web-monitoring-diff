import asyncio
import hashlib
import html5_parser
import logging
import os
import signal


logger = logging.getLogger(__name__)


def hash_content(content_bytes):
    "Create a version_hash for the content of a snapshot."
    return hashlib.sha256(content_bytes).hexdigest()


def shutdown_executor_in_loop(executor):
    """
    Safely and asynchronously shut down a ProcessPoolExecutor from within an
    event loop.

    This returns an awaitable future, but is not a coroutine itself, so it's
    safe to *not* await the result if you don't need to know when the shutdown
    is complete.

    The executor documentation suggests that calling ``shutdown(wait=False)``
    won't actually trash the executor until all pending futures are done, but
    this isn't actually true (at least not for ``ProcessPoolExecutor`` -- it
    will raise ``OSError`` moments later in an internal polling function where
    it can not be caught). To safely shutdown in an event loop, you *must* set
    ``wait=True``. This handles that for you in an easy-to-use awaitable form.

    See also: https://docs.python.org/3.7/library/concurrent.futures.html#concurrent.futures.Executor.shutdown

    Parameters
    ----------
    executor : concurrent.futures.Executor

    Returns
    -------
    shutdown : Awaitable
    """
    return asyncio.get_event_loop().run_in_executor(
        None,
        lambda: executor.shutdown(wait=True))


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


def insert_style(html, css):
    """
    Insert a new <style> tag with CSS.

    Parameters
    ----------
    html : string
    css : string

    Returns
    -------
    render : string
    """
    soup = html5_parser.parse(html, treebuilder='soup', return_root=False)

    # Ensure html includes a <head></head>.
    if not soup.head:
        head = soup.new_tag('head')
        soup.html.insert(0, head)

    style_tag = soup.new_tag("style", type="text/css")
    style_tag.string = css
    soup.head.append(style_tag)
    render = soup.prettify(formatter=None)
    return render


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
