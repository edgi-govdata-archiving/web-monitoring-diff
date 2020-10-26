from argparse import ArgumentParser
import asyncio
import codecs
import concurrent.futures
import hashlib
import inspect
import functools
import logging
import mimetypes
import os
import pycurl
import re
import cchardet
import sentry_sdk
import signal
import sys
from tornado.curl_httpclient import CurlAsyncHTTPClient, CurlError
import tornado.simple_httpclient
import tornado.httpclient
import tornado.ioloop
import tornado.web
import traceback
import web_monitoring_diff
from .. import basic_diffs, html_render_diff, html_links_diff
from ..exceptions import UndiffableContentError, UndecodableContentError
from ..utils import shutdown_executor_in_loop, Signal

logger = logging.getLogger(__name__)

# Track errors with Sentry.io. It will automatically detect the `SENTRY_DSN`
# environment variable. If not set, all its methods will operate conveniently
# as no-ops.
sentry_sdk.init(ignore_errors=[KeyboardInterrupt])
# Tornado logs any non-success response at ERROR level, which Sentry captures
# by default. We don't really want those logs.
sentry_sdk.integrations.logging.ignore_logger('tornado.access')

DIFFER_PARALLELISM = int(os.environ.get('DIFFER_PARALLELISM', 10))

# Map tokens in the REST API to functions in modules.
# The modules do not have to be part of the web_monitoring_diff package.
DIFF_ROUTES = {
    "length": basic_diffs.compare_length,
    "identical_bytes": basic_diffs.identical_bytes,
    "side_by_side_text": basic_diffs.side_by_side_text,
    "links": html_links_diff.links_diff_html,
    "links_json": html_links_diff.links_diff_json,
    # applying diff-match-patch (dmp) to strings (no tokenization)
    "html_text_dmp": basic_diffs.html_text_diff,
    "html_source_dmp": basic_diffs.html_source_diff,
    # three different approaches to the same goal:
    "html_token": html_render_diff.html_diff_render,

    # deprecated synonyms
    "links_diff": html_links_diff.links_diff,
    "html_text_diff": basic_diffs.html_text_diff,
    "html_source_diff": basic_diffs.html_source_diff,
    "html_visual_diff": html_render_diff.html_diff_render,
}

# Optional, experimental diffs.
try:
    from ..experimental import htmltreediff
    DIFF_ROUTES["html_tree"] = htmltreediff.diff
    # Deprecated synonym
    DIFF_ROUTES["html_tree_diff"] = htmltreediff.diff
except ModuleNotFoundError:
    ...

try:
    from ..experimental import htmldiffer
    DIFF_ROUTES["html_perma_cc"] = htmldiffer.diff
    # Deprecated synonym
    DIFF_ROUTES["html_differ"] = htmldiffer.diff
except ModuleNotFoundError:
    ...


# Matches a <meta> tag in HTML used to specify the character encoding:
# <meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">
# <meta charset="utf-8" />
META_TAG_PATTERN = re.compile(
    b'<meta[^>]+charset\\s*=\\s*[\'"]?([^>]*?)[ /;\'">]',
    re.IGNORECASE)

# Matches an XML prolog that specifies character encoding:
# <?xml version="1.0" encoding="ISO-8859-1"?>
XML_PROLOG_PATTERN = re.compile(
    b'<?xml\\s[^>]*encoding=[\'"]([^\'"]+)[\'"].*\?>',
    re.IGNORECASE)

MAX_BODY_SIZE = None
try:
    MAX_BODY_SIZE = int(os.environ.get('DIFFER_MAX_BODY_SIZE', 0))
    if MAX_BODY_SIZE < 0:
        print('DIFFER_MAX_BODY_SIZE must be >= 0', file=sys.stderr)
        sys.exit(1)
except ValueError:
    print('DIFFER_MAX_BODY_SIZE must be an integer', file=sys.stderr)
    sys.exit(1)


class LimitedCurlAsyncHTTPClient(CurlAsyncHTTPClient):
    """
    A customized version of Tornado's CurlAsyncHTTPClient that adds support for
    maximum response body sizes. The API is the same as that for Tornado's
    SimpleAsyncHTTPClient: set ``max_body_size`` to an integer representing the
    maximum number of bytes in a response body.
    """
    def initialize(self, max_clients=10, defaults=None, max_body_size=None):
        self.max_body_size = max_body_size
        defaults = defaults or {}
        defaults['prepare_curl_callback'] = self.prepare_curl
        super().initialize(max_clients=max_clients, defaults=defaults)

    def prepare_curl(self, curl):
        if self.max_body_size:
            # NOTE: cURL's docs suggest this doesn't work if the server doesn't
            # send a Content-Length header, but it seems to do just fine in
            # tests. ¯\_(ツ)_/¯
            curl.setopt(pycurl.MAXFILESIZE, self.max_body_size)


HTTP_CLIENT = LimitedCurlAsyncHTTPClient
if os.getenv('USE_SIMPLE_HTTP_CLIENT'):
    HTTP_CLIENT = None
tornado.httpclient.AsyncHTTPClient.configure(HTTP_CLIENT,
                                             max_body_size=MAX_BODY_SIZE)


def get_http_client():
    return tornado.httpclient.AsyncHTTPClient()


class PublicError(tornado.web.HTTPError):
    """
    Customized version of Tornado's HTTP error designed for reporting publicly
    visible error messages. Please always raise this instead of calling
    `send_error()` directly, since it lets you attach a user-visible
    explanation of what went wrong.

    Parameters
    ----------
    status_code : int, optional
        Status code for the response. Defaults to `500`.
    public_message : str, optional
        Textual description of the error. This will be publicly visible in
        production mode, unlike `log_message`.
    log_message : str, optional
        Error message written to logs and to error tracking service. Will be
        included in the HTTP response only in debug mode. Same as the
        `log_message` parameter to `tornado.web.HTTPError`, but with no
        interpolation.
    extra : dict, optional
        Dict of additional keys and values to include in the error response.
    """
    def __init__(self, status_code=500, public_message=None, log_message=None,
                 extra=None, **kwargs):
        self.extra = extra or {}

        if public_message is not None:
            if 'error' not in self.extra:
                self.extra['error'] = public_message

            if log_message is None:
                log_message = public_message

        super().__init__(status_code, log_message, **kwargs)


class MockRequest:
    "An HTTPRequest-like object for local file:/// requests."
    def __init__(self, url):
        self.url = url


class MockResponse:
    "An HTTPResponse-like object for local file:/// requests."
    def __init__(self, url, body, headers=None):
        self.request = MockRequest(url)
        self.body = body
        self.headers = headers
        self.error = None

        if self.headers is None:
            self.headers = {}

        if 'Content-Type' not in self.headers:
            self.headers.update(self._get_content_type_headers_from_url(url))

    @staticmethod
    def _get_content_type_headers_from_url(url):
        # If the extension is not recognized, assume text/html
        headers = {'Content-Type': 'text/html'}

        content_type, content_encoding = mimetypes.guess_type(url)

        if content_type is not None:
            headers['Content-Type'] = content_type

        if content_encoding is not None:
            headers['Content-Encoding'] = content_encoding

        return headers


DEBUG_MODE = os.environ.get('DIFFING_SERVER_DEBUG', 'False').strip().lower() == 'true'

VALIDATE_TARGET_CERTIFICATES = \
    os.environ.get('VALIDATE_TARGET_CERTIFICATES', 'False').strip().lower() == 'true'

access_control_allow_origin_header = \
    os.environ.get('ACCESS_CONTROL_ALLOW_ORIGIN_HEADER')


class DiffServer(tornado.web.Application):
    terminating = False

    def listen(self, port, address='', **kwargs):
        self.server = super().listen(port, address, **kwargs)
        return self.server

    async def shutdown(self, immediate=False):
        """
        Shut down the server as gracefully as possible. If `immediate` is True,
        the server will kill any in-progress diff processes immediately.
        Otherwise, diffs are allowed to try and finish.
        """
        self.terminating = True
        self.server.stop()
        await self.shutdown_differs(immediate)
        await self.server.close_all_connections()

    async def shutdown_differs(self, immediate=False):
        """Stop all child processes used for running diffs."""
        differs = self.settings.get('diff_executor')
        if differs:
            if immediate:
                # NOTE: this might be fragile since we are grabbing a private
                # attribute. One alternative is to use psutil to find all child
                # pids and indiscriminately kill them, but that has its own
                # issues.
                for child in differs._processes.values():
                    child.kill()
            else:
                await shutdown_executor_in_loop(differs)

    def handle_signal(self, signal_type, frame):
        """Handle a signal by shutting down the application and IO loop."""
        loop = tornado.ioloop.IOLoop.current()

        async def shutdown_and_stop():
            try:
                immediate = self.terminating
                method = 'immediately' if immediate else 'gracefully'
                print(f'Shutting down server {method}...')
                await self.shutdown(immediate=immediate)
                loop.stop()
                print('Shutdown complete.')
            except Exception:
                logger.exception('Failed to gracefully stop server!')
                sys.exit(1)

        loop.add_callback_from_signal(shutdown_and_stop)


class BaseHandler(tornado.web.RequestHandler):

    def set_default_headers(self):
        if access_control_allow_origin_header is not None:
            if 'allowed_origins' not in self.settings:
                self.settings['allowed_origins'] = \
                    set([origin.strip() for origin
                         in access_control_allow_origin_header.split(',')])
            req_origin = self.request.headers.get('Origin')
            if req_origin:
                allowed = self.settings.get('allowed_origins')
                if allowed and (req_origin in allowed or '*' in allowed):
                    self.set_header('Access-Control-Allow-Origin', req_origin)
            self.set_header('Access-Control-Allow-Credentials', 'true')
            self.set_header('Access-Control-Allow-Headers', 'x-requested-with')
            self.set_header('Access-Control-Allow-Methods', 'GET, OPTIONS')

    def options(self):
        # no body
        self.set_status(204)
        self.finish()


class DiffHandler(BaseHandler):
    # subclass must define `differs` attribute

    # If query parameters repeat, take last one.
    # Decode clean query parameters into unicode strings and cache the results.
    @functools.lru_cache()
    def decode_query_params(self):
        query_params = {k: v[-1].decode() for k, v in
                        self.request.arguments.items()}
        return query_params

    # Compute our own ETag header values.
    def compute_etag(self):
        # We're not actually hashing content for this, since that is expensive.
        validation_bytes = str(
            web_monitoring_diff.__version__
            + self.request.path
            + str(self.decode_query_params())
        ).encode('utf-8')

        # Uses the "weak validation" directive since we don't guarantee that future
        # responses for the same diff will be byte-for-byte identical.
        etag = f'W/"{web_monitoring_diff.utils.hash_content(validation_bytes)}"'
        return etag

    async def get(self, differ):

        # Skip a whole bunch of work if possible.
        self.set_etag_header()
        if self.check_etag_header():
            self.set_status(304)
            self.finish()
            return

        # Find the diffing function registered with the name given by `differ`.
        try:
            func = self.differs[differ]
        except KeyError:
            raise PublicError(404, f'Unknown diffing method: `{differ}`. '
                                   f'You can get a list of '
                                   f'supported differs from '
                                   f'the `/` endpoint.')

        query_params = self.decode_query_params()
        # The logic here is a bit tortured in order to allow one or both URLs
        # to be local files, while still optimizing the common case of two
        # remote URLs that we want to fetch in parallel.
        try:
            urls = {param: query_params.pop(param) for param in ('a', 'b')}
        except KeyError:
            raise PublicError(400,
                              'Malformed request. You must provide a URL '
                              'as the value for both `a` and `b` query '
                              'parameters.')

        # TODO: Add caching of fetched URIs.
        requests = [self.fetch_diffable_content(url,
                                                query_params.pop(f'{param}_hash', None),
                                                query_params)
                    for param, url in urls.items()]
        content = await asyncio.gather(*requests)

        # Pass the bytes and any remaining args to the diffing function.
        res = await self.diff(func, content[0], content[1], query_params)
        res['version'] = web_monitoring_diff.__version__
        # Echo the client's request unless the differ func has specified
        # somethine else.
        res.setdefault('type', differ)
        self.write(res)

    async def fetch_diffable_content(self, url, expected_hash, query_params):
        """
        Fetch and validate a content to diff from a given URL.
        """
        response = None

        # For testing convenience, support file:// URLs in development.
        if url.startswith('file://'):
            if os.environ.get('WEB_MONITORING_APP_ENV') == 'production':
                raise PublicError(403, 'Local files cannot be used in '
                                       'production environment.')

            with open(url[7:], 'rb') as f:
                body = f.read()
                response = MockResponse(url, body)
        # Only support HTTP(S) URLs.
        elif not url.startswith('http://') and not url.startswith('https://'):
            raise PublicError(400,
                              f'URL must use HTTP or HTTPS protocol: "{url}"',
                              'Invalid URL for upstream content',
                              extra={'url': url})
        else:
            # Include request headers defined by the query param
            # `pass_headers=HEADER_NAMES` in the upstream request. This is
            # useful for passing data like cookie headers. HEADER_NAMES is a
            # comma-separated list of HTTP header names.
            headers = {}
            header_keys = query_params.get('pass_headers')
            if header_keys:
                for header_key in header_keys.split(','):
                    header_key = header_key.strip()
                    header_value = self.request.headers.get(header_key)
                    if header_value:
                        headers[header_key] = header_value

            try:
                client = get_http_client()
                response = await client.fetch(url, headers=headers,
                                              validate_cert=VALIDATE_TARGET_CERTIFICATES)
            except ValueError as error:
                raise PublicError(400, str(error))
            # Only raised by the simple client and not by the cURL client.
            except OSError as error:
                raise PublicError(502,
                                  f'Could not fetch "{url}": {error}',
                                  'Could not fetch upstream content',
                                  extra={'url': url, 'cause': str(error)})

            # --- SIMPLE CLIENT ERRORS ----------------------------------------
            except tornado.simple_httpclient.HTTPTimeoutError:
                raise PublicError(504,
                                  f'Timed out while fetching "{url}"',
                                  'Could not fetch upstream content',
                                  extra={'url': url})
            except tornado.simple_httpclient.HTTPStreamClosedError:
                # Unfortunately we get pretty ambiguous info if the connection
                # was closed because we exceeded the max size. :(
                message = f'The connection was closed while fetching "{url}"'
                if client.max_body_size:
                    message += (f' -- this may have been caused by a large '
                                f'response (the maximum diffable response is '
                                f'{client.max_body_size} bytes)')
                raise PublicError(502,
                                  message,
                                  'Connection closed while fetching upstream',
                                  extra={'url': url,
                                         'max_size': client.max_body_size})

            # --- CURL CLIENT ERRORS ------------------------------------------
            except CurlError as error:
                # Documentation for cURL error codes:
                #   https://curl.haxx.se/libcurl/c/libcurl-errors.html
                # PyCurl has constants named `E_*` vs. libcurl's `CURLE_*`
                if error.errno == pycurl.E_URL_MALFORMAT:
                    raise PublicError(400,
                                      str(error),
                                      'Invalid URL for cURL',
                                      extra={'url': url})
                # TODO: raise a nicer error from LimitedCurlAsyncHTTPClient
                elif error.errno == pycurl.E_FILESIZE_EXCEEDED:
                    raise PublicError(502,
                                      f'Upstream response too big for "{url}"'
                                      f'(max: {client.max_body_size} bytes)',
                                      'Upstream content too big',
                                      extra={'url': url,
                                             'max_size': client.max_body_size})
                elif (error.errno == pycurl.E_COULDNT_RESOLVE_PROXY
                      or error.errno == pycurl.E_COULDNT_CONNECT
                      or error.errno == 8  # E_WEIRD_SERVER_REPLY
                      or error.errno == pycurl.E_REMOTE_ACCESS_DENIED
                      or error.errno == pycurl.E_HTTP2):
                    raise PublicError(502,
                                      f'Could not fetch "{url}": {error}',
                                      'Could not fetch upstream content',
                                      extra={'url': url, 'cause': str(error)})
                elif error.errno == pycurl.E_OPERATION_TIMEDOUT:
                    raise PublicError(504,
                                      f'Timed out while fetching "{url}"',
                                      'Could not fetch upstream content',
                                      extra={'url': url})
                else:
                    raise PublicError(502,
                                      f'Unknown error fetching "{url}"',
                                      f'Unknown error fetching upstream content: {error}',
                                      extra={'url': url})

            # --- COMMON ERRORS SUPPORTED BY ALL CLIENTS ----------------------
            except tornado.httpclient.HTTPError as error:
                # If the response is actually coming from a web archive,
                # allow error codes. The Memento-Datetime header indicates
                # the response is an archived one, and not an actual failure
                # to respond with the desired content.
                if error.response is not None and \
                        error.response.headers.get('Memento-Datetime') is not None:
                    response = error.response
                else:
                    code = error.response and error.response.code
                    raise PublicError(502,
                                      (f'Received a {code or "?"} '
                                       f'status while fetching "{url}": '
                                       f'{error}'),
                                      log_message='Could not fetch upstream content',
                                      extra={'type': 'UPSTREAM_ERROR',
                                             'url': url,
                                             'upstream_code': code})

        if response and expected_hash:
            actual_hash = hashlib.sha256(response.body).hexdigest()
            if actual_hash != expected_hash:
                raise PublicError(502,
                                  (f'Fetched content at "{url}" does not '
                                   f'match hash "{expected_hash}".'),
                                  log_message='Could not fetch upstream content',
                                  extra={'type': 'HASH_MISMATCH',
                                         'url': url,
                                         'expected_hash': expected_hash,
                                         'actual_hash': actual_hash})

        return response

    async def diff(self, func, a, b, params, tries=2):
        """
        Actually do a diff between two pieces of content, optionally retrying
        if the process pool that executes the diff breaks.
        """
        executor = self.get_diff_executor()
        loop = asyncio.get_running_loop()
        for attempt in range(tries):
            try:
                return await loop.run_in_executor(
                    executor, functools.partial(caller, func, a, b, **params))
            except concurrent.futures.process.BrokenProcessPool:
                executor = self.get_diff_executor(reset=True)

    # NOTE: this doesn't do anything async, but if we change it to do so, we
    # need to add a lock (either asyncio.Lock or tornado.locks.Lock).
    def get_diff_executor(self, reset=False):
        executor = self.settings.get('diff_executor')
        if reset or not executor:
            if executor:
                try:
                    # NOTE: we don't need await this; we just want to make sure
                    # the old executor gets cleaned up.
                    shutdown_executor_in_loop(executor)
                except Exception:
                    pass
            executor = concurrent.futures.ProcessPoolExecutor(
                DIFFER_PARALLELISM)
            self.settings['diff_executor'] = executor

        return executor

    def write_error(self, status_code, **kwargs):
        response = {'code': status_code, 'error': self._reason}

        # Handle errors that are allowed to be public
        # TODO: this error filtering should probably be in `send_error()`
        actual_error = 'exc_info' in kwargs and kwargs['exc_info'][1] or None
        if isinstance(actual_error, (UndiffableContentError, UndecodableContentError)):
            response['code'] = 422
            response['error'] = str(actual_error)

        if 'extra' in kwargs:
            response.update(kwargs['extra'])
        if isinstance(actual_error, PublicError):
            response.update(actual_error.extra)

        # Instances of PublicError and tornado.web.HTTPError won't get tracked
        # by Sentry by default, but we do want to track unexpected, server-side
        # issues. (Usually a non-HTTPError will have been raised in this case,
        # but PublicError can be used for special status codes.)
        if isinstance(actual_error, tornado.web.HTTPError) and response['code'] >= 500:
            with sentry_sdk.push_scope() as scope:
                # TODO: this breadcrumb should happen at the start of the
                # request handler, but we need to test and make sure crumbs are
                # properly attached to *this* HTTP request and don't bleed over
                # to others, since Sentry's special support for Tornado has
                # been dropped.
                scope.clear_breadcrumbs()
                headers = dict(self.request.headers)
                if 'Authorization' in headers:
                    headers['Authorization'] = '[removed]'
                sentry_sdk.add_breadcrumb(category='request', data={
                    'url': self.request.full_url(),
                    'method': self.request.method,
                    'headers': headers,
                })
                sentry_sdk.add_breadcrumb(category='response', data=response)
                scope.level = 'info'
                sentry_sdk.capture_exception(actual_error)

        # Fill in full info if configured to do so
        if self.settings.get('serve_traceback') and 'exc_info' in kwargs:
            response['error'] = str(kwargs['exc_info'][1])
            stack_lines = traceback.format_exception(*kwargs['exc_info'])
            response['stack'] = ''.join(stack_lines)

        if response['code'] != status_code:
            self.set_status(response['code'])
        self.finish(response)


def _extract_encoding(headers, content):
    encoding = None
    content_type = headers.get('Content-Type', '').lower()
    if 'charset=' in content_type:
        encoding = content_type.split('charset=')[-1]
    if not encoding:
        meta_tag_match = META_TAG_PATTERN.search(content, endpos=2048)
        if meta_tag_match:
            encoding = meta_tag_match.group(1).decode('ascii', errors='ignore')
    if not encoding:
        prolog_match = XML_PROLOG_PATTERN.search(content, endpos=2048)
        if prolog_match:
            encoding = prolog_match.group(1).decode('ascii', errors='ignore')
    if encoding:
        encoding = encoding.strip()
    if not encoding and content:
        # try to identify encoding using cchardet. Use up to 18kb of the
        # content for detection. Its not necessary to use the full content
        # as it could be huge. Also, if you use too little, detection is not
        # accurate.
        detected = cchardet.detect(content[:18432])
        if detected:
            detected_encoding = detected.get('encoding')
            if detected_encoding:
                encoding = detected_encoding.lower()

    # Handle common mistakes and errors in encoding names
    if encoding == 'iso-8559-1':
        encoding = 'iso-8859-1'
    # Windows-1252 is so commonly mislabeled, WHATWG recommends assuming it's a
    # mistake: https://encoding.spec.whatwg.org/#names-and-labels
    if encoding == 'iso-8859-1' and 'html' in content_type:
        encoding = 'windows-1252'
    # Check if the selected encoding is known. If not, fallback to default.
    try:
        codecs.lookup(encoding)
    except (LookupError, ValueError, TypeError):
        encoding = 'utf-8'
    return encoding


def _decode_body(response, name, raise_if_binary=True):
    encoding = _extract_encoding(response.headers, response.body)
    text = response.body.decode(encoding, errors='replace')
    text_length = len(text)
    if text_length == 0:
        return text

    # Replace null terminators; some differs (especially those written in C)
    # don't handle them well in the middle of a string.
    text = text.replace('\u0000', '\ufffd')

    # If a significantly large portion of the document was totally undecodable,
    # it's likely this wasn't text at all, but binary data.
    if raise_if_binary and text.count('\ufffd') / text_length > 0.25:
        raise UndecodableContentError(f'The response body of `{name}` could not be decoded as {encoding}.')

    return text


def caller(func, a, b, **query_params):
    """
    A translation layer between HTTPResponses and differ functions.

    Parameters
    ----------
    func : callable
        a 'differ' function
    a : tornado.httpclient.HTTPResponse
    b : tornado.httpclient.HTTPResponse
    **query_params
        additional parameters parsed from the REST diffing request


    The function `func` may expect required and/or optional arguments. Its
    signature serves as a dependency injection scheme, specifying what it
    needs from the HTTPResponses. The following argument names have special
    meaning:

    * a_url, b_url: URL of HTTP request
    * a_body, b_body: Raw HTTP reponse body (bytes)
    * a_text, b_text: Decoded text of HTTP response body (str)
    * a_headers, b_headers: Dict of HTTP headers

    Any other argument names in the signature will take their values from the
    REST query parameters.
    """
    # Supplement the query_parameters from the REST call with special items
    # extracted from `a` and `b`.
    query_params.setdefault('a_url', a.request.url)
    query_params.setdefault('b_url', b.request.url)
    query_params.setdefault('a_body', a.body)
    query_params.setdefault('b_body', b.body)
    query_params.setdefault('a_headers', a.headers)
    query_params.setdefault('b_headers', b.headers)

    # The differ's signature is a dependency injection scheme.
    sig = inspect.signature(func)

    raise_if_binary = not query_params.get('ignore_decoding_errors', False)
    if 'a_text' in sig.parameters:
        query_params.setdefault(
            'a_text',
            _decode_body(a, 'a', raise_if_binary=raise_if_binary))
    if 'b_text' in sig.parameters:
        query_params.setdefault(
            'b_text',
            _decode_body(b, 'b', raise_if_binary=raise_if_binary))

    kwargs = dict()
    for name, param in sig.parameters.items():
        try:
            kwargs[name] = query_params[name]
        except KeyError:
            if param.default is inspect._empty:
                # This is a required argument.
                raise KeyError("{} requires a parameter {} which was not "
                               "provided in the query"
                               "".format(func.__name__, name))
    return func(**kwargs)


class IndexHandler(BaseHandler):

    async def get(self):
        # TODO Show swagger API or Markdown instead.
        info = {'diff_types': list(DIFF_ROUTES),
                'version': web_monitoring_diff.__version__}
        self.write(info)


class HealthCheckHandler(BaseHandler):

    async def get(self):
        # TODO Include more information about health here.
        # The 200 repsonse code with an empty object is just a liveness check.
        self.write({})


def make_app():
    """
    Create and return a Tornado application object that serves diffs.
    """
    class BoundDiffHandler(DiffHandler):
        differs = DIFF_ROUTES

    return DiffServer([
        (r"/healthcheck", HealthCheckHandler),
        (r"/([A-Za-z0-9_]+)", BoundDiffHandler),
        (r"/", IndexHandler),
    ], debug=DEBUG_MODE, compress_response=True,
       diff_executor=None)


def start_app(port):
    """
    Create and start the diff server on a given port.

    This is a blocking call -- it starts an event loop for the server and does
    not return until the server has shut down. For more control, use
    :func:`create_app`.

    Parameters
    ----------
    port : int
        The port to listen on.
    """
    app = make_app()
    print(f'Starting server on port {port}')
    app.listen(port)
    with Signal((signal.SIGINT, signal.SIGTERM), app.handle_signal):
        tornado.ioloop.IOLoop.current().start()


def cli():
    """
    Start the diff server from the CLI. This will parse the current process's
    arguments, start an event loop, and begin serving.
    """
    parser = ArgumentParser(description='Start a diffing server.')
    parser.add_argument('--version', action='store_true',
                        help='Show version information')
    parser.add_argument('--port', type=int, default=8888,
                        help='Port to listen on')
    arguments = parser.parse_args()

    if arguments.version:
        print(web_monitoring_diff.__version__)
        return

    start_app(arguments.port)


if __name__ == '__main__':
    cli()
