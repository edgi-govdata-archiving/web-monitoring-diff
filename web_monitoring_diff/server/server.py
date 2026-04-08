from argparse import ArgumentParser
import asyncio
import codecs
import hashlib
import inspect
import functools
import logging
import os
import pycurl
import re
import sentry_sdk
import signal
import sys
from dotenv import load_dotenv
from tornado.curl_httpclient import CurlAsyncHTTPClient, CurlError
import tornado.simple_httpclient
import tornado.httpclient
import tornado.ioloop
import tornado.web
import web_monitoring_diff
from .mock_http import MockResponse
from .. import basic_diffs, html_render_diff, html_links_diff
from ..exceptions import UndecodableContentError
from ..utils import Signal
from .executor import DiffExecutorManager, DiffPoolError
import json

# Where possible, use cchardet (or faust-cchardet) for performance.
# Unfortunately these aren't supported in the latest Python vesions, so we also
# fall back to something in pure Python for those cases. There are a few
# options there, but `chardet` offers the best accuracy/performance tradeoff
# when operating on truncated/small content (we always truncate).
# (Performance measurements as of mid-2024.)
try:
    import cchardet as chardet
except ImportError:
    import chardet

logger = logging.getLogger(__name__)

# Track errors with Sentry.io. It will automatically detect the `SENTRY_DSN`
# environment variable. If not set, all its methods will operate conveniently
# as no-ops.
sentry_sdk.init(ignore_errors=[KeyboardInterrupt])
# Tornado logs any non-success response at ERROR level, which Sentry captures
# by default. We don't really want those logs.
sentry_sdk.integrations.logging.ignore_logger("tornado.access")

DIFFER_PARALLELISM = int(os.environ.get("DIFFER_PARALLELISM", 10))
MAX_DIFFS_PER_WORKER = max(int(os.environ.get("MAX_DIFFS_PER_WORKER", 0)), 0)
RESTART_BROKEN_DIFFER = (
    os.environ.get("RESTART_BROKEN_DIFFER", "False").strip().lower() == "true"
)

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
    "html_token": html_render_diff.html_diff_render,
}

# Optional, experimental diffs.
try:
    from ..experimental import htmltreediff

    DIFF_ROUTES["html_tree"] = htmltreediff.diff
except ModuleNotFoundError:
    ...

try:
    from ..experimental import htmldiffer

    DIFF_ROUTES["html_perma_cc"] = htmldiffer.diff
except ModuleNotFoundError:
    ...


# Matches a <meta> tag in HTML used to specify the character encoding:
# <meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">
# <meta charset="utf-8" />
META_TAG_PATTERN = re.compile(
    b"<meta[^>]+charset\\s*=\\s*['\"]?([^>]*?)[ /;'\">]", re.IGNORECASE
)

# Matches an XML prolog that specifies character encoding:
# <?xml version="1.0" encoding="ISO-8859-1"?>
XML_PROLOG_PATTERN = re.compile(
    b"<\\?xml\\s[^>]*encoding=['\"]([^'\"]+)['\"].*\\?>", re.IGNORECASE
)

MAX_BODY_SIZE = None
try:
    MAX_BODY_SIZE = int(os.environ.get("DIFFER_MAX_BODY_SIZE", 0))
    if MAX_BODY_SIZE < 0:
        print("DIFFER_MAX_BODY_SIZE must be >= 0", file=sys.stderr)
        sys.exit(1)
except ValueError:
    print("DIFFER_MAX_BODY_SIZE must be an integer", file=sys.stderr)
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
        defaults["prepare_curl_callback"] = self.prepare_curl
        super().initialize(max_clients=max_clients, defaults=defaults)

    def prepare_curl(self, curl):
        if self.max_body_size:
            # NOTE: cURL's docs suggest this doesn't work if the server doesn't
            # send a Content-Length header, but it seems to do just fine in
            # tests. ¯\_(ツ)_/¯
            curl.setopt(pycurl.MAXFILESIZE, self.max_body_size)


HTTP_CLIENT = LimitedCurlAsyncHTTPClient
if os.getenv("USE_SIMPLE_HTTP_CLIENT"):
    HTTP_CLIENT = None
tornado.httpclient.AsyncHTTPClient.configure(HTTP_CLIENT, max_body_size=MAX_BODY_SIZE)


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
    status_code : int, default: 500
        Status code for the response.
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

    def __init__(
        self,
        status_code=500,
        public_message=None,
        log_message=None,
        extra=None,
        **kwargs,
    ):
        self.extra = extra or {}

        if public_message is not None:
            if "error" not in self.extra:
                self.extra["error"] = public_message

            if log_message is None:
                log_message = public_message

        super().__init__(status_code, log_message, **kwargs)


DEBUG_MODE = os.environ.get("DIFFING_SERVER_DEBUG", "False").strip().lower() == "true"

VALIDATE_TARGET_CERTIFICATES = (
    os.environ.get("VALIDATE_TARGET_CERTIFICATES", "False").strip().lower() == "true"
)

access_control_allow_origin_header = os.environ.get(
    "ACCESS_CONTROL_ALLOW_ORIGIN_HEADER"
)


def initialize_diff_worker():
    signal.signal(signal.SIGINT, signal.SIG_IGN)


class DiffServer(tornado.web.Application):
    def __init__(self, handlers, **settings):
        super().__init__(handlers, **settings)
        self.terminating = False
        self.server = None
        self._executor_manager = None

    @property
    def executor_manager(self):
        if self._executor_manager is None:
            self._executor_manager = DiffExecutorManager(
                parallelism=DIFFER_PARALLELISM,
                max_diffs=MAX_DIFFS_PER_WORKER,
                initializer=initialize_diff_worker,
                restart_on_fail=RESTART_BROKEN_DIFFER,
            )
        return self._executor_manager

    def listen(self, port, address="", **kwargs):
        self.server = super().listen(port, address, **kwargs)
        return self.server

    async def shutdown(self, immediate=False):
        self.terminating = True
        if self.server:
            self.server.stop()
        if self._executor_manager:
            await self._executor_manager.shutdown(immediate)
        if self.server:
            await self.server.close_all_connections()

    async def quit(self, immediate=False, code=0):
        await self.shutdown(immediate=immediate)
        tornado.ioloop.IOLoop.current().stop()
        if code:
            sys.exit(code)

    def handle_signal(self, signal_type, frame):
        loop = tornado.ioloop.IOLoop.current()

        async def shutdown_and_stop():
            try:
                await self.shutdown(immediate=self.terminating)
                loop.stop()
            except Exception:
                logger.exception("Failed to stop server!")
                sys.exit(1)

        loop.add_callback_from_signal(shutdown_and_stop)


class BaseHandler(tornado.web.RequestHandler):
    def set_default_headers(self):

        if access_control_allow_origin_header is not None:
            if "allowed_origins" not in self.settings:
                self.settings["allowed_origins"] = set(
                    [
                        origin.strip()
                        for origin in access_control_allow_origin_header.split(",")
                    ]
                )
            req_origin = self.request.headers.get("Origin")
            if req_origin:
                allowed = self.settings.get("allowed_origins")
                if allowed and (req_origin in allowed or "*" in allowed):
                    self.set_header("Access-Control-Allow-Origin", req_origin)
            self.set_header("Access-Control-Allow-Credentials", "true")
            self.set_header("Access-Control-Allow-Headers", "x-requested-with")
            self.set_header("Access-Control-Allow-Methods", "GET, OPTIONS")

    def write_error(self, status_code, **kwargs):
        """Force Tornado to return JSON on errors instead of HTML pages."""
        self.set_header("Content-Type", "application/json")
        response = {"error": self._reason, "code": status_code}
        if "exc_info" in kwargs:
            exc = kwargs["exc_info"][1]
            if isinstance(exc, PublicError) and exc.extra:
                response.update(exc.extra)
        self.finish(json.dumps(response))


class DiffHandler(BaseHandler):
    def get_diff_executor(self, reset=False):
        """Shim for existing tests."""
        return self.application.executor_manager.get_executor(force_reset=reset)

    @functools.lru_cache()
    def decode_query_params(self):
        return {k: v[-1].decode() for k, v in self.request.arguments.items()}

    def compute_etag(self):
        validation_bytes = str(
            web_monitoring_diff.__version__
            + self.request.path
            + str(self.decode_query_params())
        ).encode("utf-8")
        return f'W/"{web_monitoring_diff.utils.hash_content(validation_bytes)}"'

    async def get(self, differ):
        self.set_etag_header()
        if self.check_etag_header():
            self.set_status(304)
            self.finish()
            return
        try:
            func = self.differs[differ]
        except KeyError:
            raise PublicError(404, f"Unknown diffing method: `{differ}`.")
        query_params = self.decode_query_params()
        try:
            urls = {param: query_params.pop(param) for param in ("a", "b")}
        except KeyError:
            raise PublicError(400, "Provide a URL for both `a` and `b`.")
        requests = [
            self.fetch_diffable_content(
                url, query_params.pop(f"{param}_hash", None), query_params
            )
            for param, url in urls.items()
        ]
        content = await asyncio.gather(*requests)
        res = await self.diff(func, content[0], content[1], query_params)
        res["version"] = web_monitoring_diff.__version__
        res.setdefault("type", differ)
        self.write(res)

    async def diff(self, func, a, b, params, tries=2):
        try:
            return await self.application.executor_manager.run_diff(
                caller, func, a, b, params, tries
            )
        except DiffPoolError:
            if not self.application.executor_manager.restart_on_fail:
                tornado.ioloop.IOLoop.current().add_callback(
                    self.application.quit, code=10
                )
            raise

    async def fetch_diffable_content(self, url, expected_hash, query_params):
        response = None
        if url.startswith("file://"):
            if os.environ.get("WEB_MONITORING_APP_ENV") == "production":
                raise PublicError(403, "Local files forbidden in production.")
            with open(url[7:], "rb") as f:
                response = MockResponse(url, f.read())
        elif not url.startswith(("http://", "https://")):
            raise PublicError(400, f'URL must use HTTP or HTTPS: "{url}"')
        else:
            headers = {}
            header_keys = query_params.get("pass_headers")
            if header_keys:
                for key in header_keys.split(","):
                    val = self.request.headers.get(key.strip())
                    if val:
                        headers[key.strip()] = val
            try:
                client = get_http_client()
                response = await client.fetch(
                    url, headers=headers, validate_cert=VALIDATE_TARGET_CERTIFICATES
                )
            except (tornado.httpclient.HTTPError, CurlError) as error:
                if (
                    isinstance(error, tornado.httpclient.HTTPError)
                    and error.response
                    and error.response.headers.get("Memento-Datetime")
                ):
                    response = error.response
                else:
                    status_code = 502
                    if getattr(error, "code", 0) == 599:
                        status_code = 504
                        if "Maximum file size" in str(error):
                            status_code = 502
                    raise PublicError(status_code, f'Fetch error for "{url}": {error}')
            except OSError as error:
                raise PublicError(502, f'Connection error for "{url}": {error}')

        if response and expected_hash:
            if hashlib.sha256(response.body).hexdigest() != expected_hash:
                raise PublicError(502, f'hash mismatch for "{url}"')
        return response


def _extract_encoding(headers, content):
    encoding = None
    content_type = headers.get("Content-Type", "").lower()
    if "charset=" in content_type:
        encoding = content_type.split("charset=")[-1]
    if not encoding:
        meta_tag_match = META_TAG_PATTERN.search(content, endpos=2048)
        if meta_tag_match:
            encoding = meta_tag_match.group(1).decode("ascii", errors="ignore")
    if not encoding:
        prolog_match = XML_PROLOG_PATTERN.search(content, endpos=2048)
        if prolog_match:
            encoding = prolog_match.group(1).decode("ascii", errors="ignore")
    if encoding:
        encoding = encoding.strip()
    if not encoding and content:
        # try to identify encoding using chardet. Use up to 18kb of the
        # content for detection. Its not necessary to use the full content
        # as it could be huge. Also, if you use too little, detection is not
        # accurate.
        detected = chardet.detect(content[:18432])
        if detected:
            detected_encoding = detected.get("encoding")
            if detected_encoding:
                encoding = detected_encoding.lower()

    # Handle common mistakes and errors in encoding names
    if encoding == "iso-8559-1":
        encoding = "iso-8859-1"
    # Windows-1252 is so commonly mislabeled, WHATWG recommends assuming it's a
    # mistake: https://encoding.spec.whatwg.org/#names-and-labels
    if encoding == "iso-8859-1" and "html" in content_type:
        encoding = "windows-1252"
    # Check if the selected encoding is known. If not, fallback to default.
    try:
        codecs.lookup(encoding)
    except (LookupError, ValueError, TypeError):
        encoding = "utf-8"
    return encoding


def _decode_body(response, name, raise_if_binary=True):
    encoding = _extract_encoding(response.headers, response.body)
    text = response.body.decode(encoding, errors="replace")
    text_length = len(text)
    if text_length == 0:
        return text

    # Replace null terminators; some differs (especially those written in C)
    # don't handle them well in the middle of a string.
    text = text.replace("\u0000", "\ufffd")

    # If a significantly large portion of the document was totally undecodable,
    # it's likely this wasn't text at all, but binary data.
    if raise_if_binary and text.count("\ufffd") / text_length > 0.25:
        raise UndecodableContentError(
            f"The response body of `{name}` could not be decoded as {encoding}."
        )

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
    query_params.setdefault("a_url", a.request.url)
    query_params.setdefault("b_url", b.request.url)
    query_params.setdefault("a_body", a.body)
    query_params.setdefault("b_body", b.body)
    query_params.setdefault("a_headers", a.headers)
    query_params.setdefault("b_headers", b.headers)

    # The differ's signature is a dependency injection scheme.
    sig = inspect.signature(func)

    raise_if_binary = not query_params.get("ignore_decoding_errors", False)

    try:
        if "a_text" in sig.parameters:
            query_params.setdefault(
                "a_text", _decode_body(a, "a", raise_if_binary=raise_if_binary)
            )
        if "b_text" in sig.parameters:
            query_params.setdefault(
                "b_text", _decode_body(b, "b", raise_if_binary=raise_if_binary)
            )
    except UndecodableContentError as e:
        raise PublicError(422, str(e))

    kwargs = dict()
    for name, param in sig.parameters.items():
        try:
            kwargs[name] = query_params[name]
        except KeyError:
            if param.default is inspect._empty:
                # This is a required argument.
                raise KeyError(
                    "{} requires a parameter {} which was not "
                    "provided in the query"
                    "".format(func.__name__, name)
                )
    return func(**kwargs)


def make_app():
    class BoundDiffHandler(DiffHandler):
        differs = DIFF_ROUTES

    return DiffServer(
        [
            (r"/healthcheck", HealthCheckHandler),
            (r"/([A-Za-z0-9_]+)", BoundDiffHandler),
            (r"/", IndexHandler),
        ],
        debug=DEBUG_MODE,
        compress_response=True,
    )


class IndexHandler(BaseHandler):

    async def get(self):
        # TODO Show swagger API or Markdown instead.
        info = {
            "diff_types": list(DIFF_ROUTES),
            "version": web_monitoring_diff.__version__,
        }
        self.write(info)


class HealthCheckHandler(BaseHandler):

    async def get(self):
        # TODO Include more information about health here.
        # The 200 repsonse code with an empty object is just a liveness check.
        self.write({})


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
    print(f"Starting server on port {port}")
    app.listen(port)
    with Signal((signal.SIGINT, signal.SIGTERM), app.handle_signal):
        tornado.ioloop.IOLoop.current().start()


def cli():
    """
    Start the diff server from the CLI. This will parse the current process's
    arguments, start an event loop, and begin serving.
    """
    parser = ArgumentParser(description="Start a diffing server.")
    parser.add_argument(
        "--version", action="store_true", help="Show version information"
    )
    parser.add_argument("--port", type=int, default=8888, help="Port to listen on")
    arguments = parser.parse_args()

    if arguments.version:
        print(web_monitoring_diff.__version__)
        return

    # Update os.environ with values from `.env` file, if present.
    load_dotenv()

    start_app(arguments.port)


if __name__ == "__main__":
    cli()
