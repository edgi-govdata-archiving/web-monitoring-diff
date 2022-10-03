import asyncio
import concurrent.futures
from concurrent.futures.process import BrokenProcessPool, ProcessPoolExecutor
import json
import os
import unittest
from pathlib import Path
import re
import tempfile
from tornado.testing import AsyncHTTPTestCase, bind_unused_port
from unittest.mock import patch
import web_monitoring_diff.server.server as df
from web_monitoring_diff.exceptions import UndecodableContentError
import web_monitoring_diff
from tornado.escape import utf8
from tornado.httpclient import HTTPResponse, AsyncHTTPClient
from tornado.httpserver import HTTPServer
from tornado.httputil import HTTPHeaders
import tornado.web
from io import BytesIO


def patch_http_client(**kwargs):
    """
    Create HTTP clients in the diffing server with the specified parameters
    during this patch. Can be a function decorator or context manager.
    """
    def get_client():
        return tornado.httpclient.AsyncHTTPClient(force_instance=True,
                                                  **kwargs)
    return patch.object(df, 'get_http_client', get_client)


class DiffingServerTestCase(AsyncHTTPTestCase):

    def get_app(self):
        return df.make_app()

    def json_check(self, response):
        json_header = response.headers.get('Content-Type').split(';')
        self.assertEqual(json_header[0], 'application/json')

        json_response = json.loads(response.body)
        self.assertTrue(isinstance(json_response['code'], int))
        self.assertTrue(isinstance(json_response['error'], str))


class DiffingServerIndexTest(DiffingServerTestCase):
    def test_version(self):
        response = self.fetch('/')
        json_response = json.loads(response.body)
        assert json_response['version'] == web_monitoring_diff.__version__


class DiffingServerLocalHandlingTest(DiffingServerTestCase):

    def test_one_local(self):
        with tempfile.NamedTemporaryFile() as a:
            response = self.fetch('/identical_bytes?'
                                  f'a=file://{a.name}&b=https://example.org')
            self.assertEqual(response.code, 200)

    def test_both_local(self):
        with tempfile.NamedTemporaryFile() as a:
            with tempfile.NamedTemporaryFile() as b:
                response = self.fetch('/identical_bytes?'
                                      f'a=file://{a.name}&b=file://{b.name}')
                self.assertEqual(response.code, 200)


class DiffingServerEtagTest(DiffingServerTestCase):
    def test_etag_validation(self):
        with tempfile.NamedTemporaryFile() as a:
            with tempfile.NamedTemporaryFile() as b:
                cold_response = self.fetch('/html_token?format=json&include=all&'
                        f'a=file://{a.name}&b=file://{b.name}')
                self.assertEqual(cold_response.code, 200)

                etag = cold_response.headers.get('Etag')

                warm_response = self.fetch('/html_token?format=json&include=all&'
                        f'a=file://{a.name}&b=file://{b.name}',
                                           headers={'If-None-Match': etag,
                                           'Accept': 'application/json'})
                self.assertEqual(warm_response.code, 304)

                mismatch_response = self.fetch('/html_token?format=json&include=all&'
                        f'a=file://{a.name}&b=file://{b.name}',
                                           headers={'If-None-Match': 'Stale Value',
                                           'Accept': 'application/json'})
                self.assertEqual(mismatch_response.code, 200)


class DiffingServerHealthCheckHandlingTest(DiffingServerTestCase):

    def test_healthcheck(self):
        response = self.fetch('/healthcheck')
        self.assertEqual(response.code, 200)


class DiffingServerFetchTest(DiffingServerTestCase):

    def test_pass_headers(self):
        mock = MockAsyncHttpClient()
        with patch.object(df, 'get_http_client', return_value=mock):
            mock.respond_to(r'/a$')
            mock.respond_to(r'/b$')

            self.fetch('/html_source_dmp?'
                       'pass_headers=Authorization,%20User-Agent&'
                       'a=https://example.org/a&b=https://example.org/b',
                       headers={'User-Agent': 'Some Agent',
                                'Authorization': 'Bearer xyz',
                                'Accept': 'application/json'})

            a_headers = mock.requests['https://example.org/a'].headers
            assert a_headers.get('User-Agent') == 'Some Agent'
            assert a_headers.get('Authorization') == 'Bearer xyz'
            assert a_headers.get('Accept') != 'application/json'

            b_headers = mock.requests['https://example.org/b'].headers
            assert b_headers.get('User-Agent') == 'Some Agent'
            assert b_headers.get('Authorization') == 'Bearer xyz'
            assert b_headers.get('Accept') != 'application/json'


class DiffingServerExceptionHandlingTest(DiffingServerTestCase):

    def test_local_file_disallowed_in_production(self):
        original = os.environ.get('WEB_MONITORING_APP_ENV')
        os.environ['WEB_MONITORING_APP_ENV'] = 'production'
        try:
            with tempfile.NamedTemporaryFile() as a:
                response = self.fetch('/identical_bytes?'
                                      f'a=file://{a.name}&b=https://example.org')
                self.assertEqual(response.code, 403)
        finally:
            if original is None:
                del os.environ['WEB_MONITORING_APP_ENV']
            else:
                os.environ['WEB_MONITORING_APP_ENV'] = original

    def test_invalid_url_a_format(self):
        response = self.fetch('/html_token?format=json&include=all&'
                              'a=example.org&b=https://example.org')
        self.json_check(response)
        self.assertEqual(response.code, 400)
        self.assertFalse(response.headers.get('Etag'))

    def test_invalid_url_b_format(self):
        response = self.fetch('/html_token?format=json&include=all&'
                              'a=https://example.org&b=example.org')
        self.json_check(response)
        self.assertEqual(response.code, 400)
        self.assertFalse(response.headers.get('Etag'))

    def test_invalid_diffing_method(self):
        response = self.fetch('/non_existing?format=json&include=all&'
                              'a=example.org&b=https://example.org')
        self.json_check(response)
        self.assertEqual(response.code, 404)
        self.assertFalse(response.headers.get('Etag'))

    def test_missing_url_a(self):
        response = self.fetch('/html_token?format=json&include=all&'
                              'b=https://example.org')
        self.json_check(response)
        self.assertEqual(response.code, 400)
        self.assertFalse(response.headers.get('Etag'))

    def test_missing_url_b(self):
        response = self.fetch('/html_token?format=json&include=all&'
                              'a=https://example.org')
        self.json_check(response)
        self.assertEqual(response.code, 400)
        self.assertFalse(response.headers.get('Etag'))

    def test_not_reachable_url_a(self):
        response = self.fetch('/html_token?format=json&include=all&'
                              'a=https://eeexample.org&b=https://example.org')
        self.json_check(response)
        self.assertEqual(response.code, 502)
        self.assertFalse(response.headers.get('Etag'))

    def test_not_reachable_url_b(self):
        response = self.fetch('/html_token?format=json&include=all&'
                              'a=https://example.org&b=https://eeexample.org')
        self.json_check(response)
        self.assertEqual(response.code, 502)
        self.assertFalse(response.headers.get('Etag'))

    @patch_http_client(defaults=dict(request_timeout=0.5))
    def test_timeout_upstream(self):
        async def responder(handler):
            await asyncio.sleep(1)
            handler.write('HELLO!'.encode('utf-8'))

        with SimpleHttpServer(responder) as server:
            response = self.fetch('/html_source_dmp?'
                                  f'a={server.url("/whatever1")}&'
                                  f'b={server.url("/whatever2")}')
            assert response.code == 504

    def test_missing_params_caller_func(self):
        response = self.fetch('http://example.org/')
        with self.assertRaises(KeyError):
            df.caller(mock_diffing_method, response, response)

    def test_a_is_404(self):
        response = self.fetch('/html_token?format=json&include=all'
                              '&a=http://httpstat.us/404'
                              '&b=https://example.org')
        # The error is upstream, but the message should indicate it was a 404.
        self.assertEqual(response.code, 502)
        assert '404' in json.loads(response.body)['error']
        self.assertFalse(response.headers.get('Etag'))
        self.json_check(response)

    def test_accepts_errors_from_web_archives(self):
        """
        If a page has HTTP status != 2xx but comes from a web archive,
        we proceed with diffing.
        """
        mock = MockAsyncHttpClient()
        with patch.object(df, 'get_http_client', return_value=mock):
            mock.respond_to(r'/error$', code=404, headers={'Memento-Datetime': 'Tue Sep 25 2018 03:38:50'})
            mock.respond_to(r'/success$')

            response = self.fetch('/html_token?format=json&include=all'
                                  '&a=https://archive.org/20180925033850/http://httpstat.us/error'
                                  '&b=https://example.org/success')

            self.assertEqual(response.code, 200)
            assert 'change_count' in json.loads(response.body)

    @patch('web_monitoring_diff.server.server.access_control_allow_origin_header', '*')
    def test_check_cors_headers(self):
        """
        Since we have set Access-Control-Allow-Origin: * on app init,
        the response should have a list of HTTP headers required by CORS.
        Access-Control-Allow-Origin value equals request Origin header because
        we use setting `access_control_allow_origin_header='*'`.
        """
        response = self.fetch('/html_token?format=json&include=all'
                              '&a=https://example.org&b=https://example.org',
                              headers={'Accept': 'application/json',
                                       'Origin': 'http://test.com'})
        assert response.headers.get('Access-Control-Allow-Origin') == 'http://test.com'
        assert response.headers.get('Access-Control-Allow-Credentials') == 'true'
        assert response.headers.get('Access-Control-Allow-Headers') == 'x-requested-with'
        assert response.headers.get('Access-Control-Allow-Methods') == 'GET, OPTIONS'

    @patch('web_monitoring_diff.server.server.access_control_allow_origin_header',
           'http://one.com,http://two.com,http://three.com')
    def test_cors_origin_header(self):
        """
        The allowed origins is a list of URLs. If the request has HTTP
        header `Origin` as one of them, the response `Access-Control-Allow-Origin`
        should have the same value. If not, there shouldn't be any such header
        at all.
        This is necessary for CORS requests with credentials to work properly.
        """
        response = self.fetch('/html_token?format=json&include=all'
                              '&a=https://example.org&b=https://example.org',
                              headers={'Accept': 'application/json',
                                       'Origin': 'http://two.com'})
        assert response.headers.get('Access-Control-Allow-Origin') == 'http://two.com'

    def test_decode_empty_bodies(self):
        response = mock_tornado_request('empty.txt')
        df._decode_body(response, 'a')

    def test_poorly_encoded_content(self):
        response = mock_tornado_request('poorly_encoded_utf8.txt')
        df._decode_body(response, 'a')

    def test_undecodable_content(self):
        response = mock_tornado_request('simple.pdf')
        with self.assertRaises(UndecodableContentError):
            df._decode_body(response, 'a')

    def test_fetch_undecodable_content(self):
        response = self.fetch('/html_source_dmp?format=json&'
                              f'a=file://{fixture_path("poorly_encoded_utf8.txt")}&'
                              f'b=file://{fixture_path("simple.pdf")}')
        self.json_check(response)
        assert response.code == 422
        self.assertFalse(response.headers.get('Etag'))

    def test_treats_unknown_encoding_as_ascii(self):
        response = mock_tornado_request('unknown_encoding.html')
        df._decode_body(response, 'a')

    def test_extract_encoding_bad_headers(self):
        headers = {'Content-Type': '  text/html; charset=iso-8859-7'}
        assert df._extract_encoding(headers, b'') == 'iso-8859-7'
        headers = {'Content-Type': 'text/xhtml;CHARSET=iso-8859-5 '}
        assert df._extract_encoding(headers, b'') == 'iso-8859-5'
        headers = {'Content-Type': '\x94Invalid\x0b'}
        assert df._extract_encoding(headers, b'') == 'utf-8'

    def test_extract_encoding_from_body(self):
        # Polish content without any content-type headers or meta tag.
        headers = {}
        body = """<html><head><title>TITLE</title></head>
        <i>czyli co zrobić aby zobaczyć w tekstach polskie litery.</i>
        Obowiązku czytania nie ma, ale wiele może wyjaśnić.
        <body></body>""".encode('iso-8859-2')
        assert df._extract_encoding(headers, body) == 'iso-8859-2'

    def test_diff_content_with_null_bytes(self):
        response = self.fetch('/html_source_dmp?format=json&'
                              f'a=file://{fixture_path("has_null_byte.txt")}&'
                              f'b=file://{fixture_path("has_null_byte.txt")}')
        assert response.code == 200

    def test_validates_good_hashes(self):
        response = self.fetch('/html_source_dmp?format=json&'
                              f'a=file://{fixture_path("empty.txt")}&'
                              'a_hash=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855&'
                              f'b=file://{fixture_path("empty.txt")}&'
                              'b_hash=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
        assert response.code == 200

    def test_validates_bad_hashes(self):
        response = self.fetch('/html_source_dmp?format=json&'
                              f'a=file://{fixture_path("empty.txt")}&'
                              'a_hash=f3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855&'
                              f'b=file://{fixture_path("empty.txt")}&'
                              'b_hash=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
        assert response.code == 502
        assert 'hash' in json.loads(response.body)['error']


class DiffingServerResponseSizeTest(DiffingServerTestCase):
    @patch_http_client(max_body_size=100 * 1024)
    def test_succeeds_if_response_is_small_enough(self):
        async def responder(handler):
            text = (80 * 1024) * 'x'
            handler.write(text.encode('utf-8'))

        with SimpleHttpServer(responder) as server:
            response = self.fetch('/html_source_dmp?'
                                  f'a={server.url("/whatever1")}&'
                                  f'b={server.url("/whatever2")}')
            assert response.code == 200

    @patch_http_client(max_body_size=100 * 1024)
    def test_stops_if_response_is_too_big(self):
        async def responder(handler):
            text = (110 * 1024) * 'x'
            handler.write(text.encode('utf-8'))

        with SimpleHttpServer(responder) as server:
            response = self.fetch('/html_source_dmp?'
                                  f'a={server.url("/whatever1")}&'
                                  f'b={server.url("/whatever2")}')
            assert response.code == 502

    @patch_http_client(max_body_size=100 * 1024)
    def test_stops_reading_early_when_content_length_is_a_lie(self):
        async def responder(handler):
            # Tornado tries to be careful and prevent us from sending more
            # content than we said we would, so we have to subvert it by
            # flushing the headers and resetting its expectations before
            # writing our output.
            handler.set_header('Content-Length', '1024')
            handler.flush()
            handler.request.connection._expected_content_remaining = None
            text = (110 * 1024) * 'x'
            handler.write(text.encode('utf-8'))

        with SimpleHttpServer(responder) as server:
            response = self.fetch('/html_source_dmp?'
                                  f'a={server.url("/whatever1")}&'
                                  f'b={server.url("/whatever2")}')
            # Even though the response was longer than the max_body_size,
            # the client should have stopped reading when it hit the number
            # of bytes set in the Content-Length, header, which is less
            # than the client's limit. So what should actually happen is a
            # successful diff of the first <Content-Length> bytes of the
            # responses.
            assert response.code == 200
            # The responses should be the same, and should only be
            # <Content-Length> bytes long (all our chars are basic ASCII
            # in this case, so len(bytes) == len(characters)).
            result = json.loads(response.body)
            assert result['change_count'] == 0
            assert len(result['diff'][0][1]) == 1024


class BrokenProcessPoolExecutor(concurrent.futures.Executor):
    "Fake process pool that only raises BrokenProcessPool exceptions."
    submit_count = 0

    def __init__(max_workers=None, mp_context=None, initializer=None, initargs=()):
        return super().__init__()

    def submit(self, fn, *args, **kwargs):
        self.submit_count += 1
        result = concurrent.futures.Future()
        result.set_exception(BrokenProcessPool(
            'This pool is broken, yo'
        ))
        return result


class ExecutionPoolTestCase(DiffingServerTestCase):
    def fetch_async(self, path, raise_error=False, **kwargs):
        "Like AyncHTTPTestCase.fetch, but async."
        url = self.get_url(path)
        return self.http_client.fetch(url, raise_error=raise_error, **kwargs)

    def test_rebuilds_process_pool_when_broken(self):
        # Get a custom executor that will always fail the first time, but get
        # a real one that will succeed afterward.
        did_get_executor = False
        def get_executor(self, reset=False):
            nonlocal did_get_executor
            if did_get_executor:
                return ProcessPoolExecutor(1)
            else:
                did_get_executor = True
                return BrokenProcessPoolExecutor()

        with patch.object(df.DiffHandler, 'get_diff_executor', get_executor):
            response = self.fetch('/html_source_dmp?format=json&'
                                  f'a=file://{fixture_path("empty.txt")}&'
                                  f'b=file://{fixture_path("empty.txt")}')
            assert response.code == 200
            assert did_get_executor == True

    @patch.object(df.DiffServer, "quit")
    def test_diff_returns_error_if_process_pool_repeatedly_breaks(self, _):
        # Set a custom executor that will always fail.
        def get_executor(self, reset=False):
            return BrokenProcessPoolExecutor()

        with patch.object(df.DiffHandler, 'get_diff_executor', get_executor):
            response = self.fetch('/html_source_dmp?format=json&'
                                  f'a=file://{fixture_path("empty.txt")}&'
                                  f'b=file://{fixture_path("empty.txt")}')
            self.json_check(response)
            assert response.code == 500

    @tornado.testing.gen_test
    async def test_rebuilds_process_pool_cooperatively(self):
        """
        Make sure that two parallel diffing failures only cause the process
        pool to be rebuilt once, not multiple times.
        """
        # Get a custom executor that will always fail the first time, but get
        # a real one that will succeed afterward.
        executor_resets = 0
        good_executor = ProcessPoolExecutor(1)
        bad_executor = BrokenProcessPoolExecutor()
        def get_executor(self, reset=False):
            nonlocal executor_resets
            if reset:
                executor_resets += 1
            if executor_resets > 0:
                return good_executor
            else:
                return bad_executor

        with patch.object(df.DiffHandler, 'get_diff_executor', get_executor):
            one = self.fetch_async('/html_source_dmp?format=json&'
                                   f'a=file://{fixture_path("empty.txt")}&'
                                   f'b=file://{fixture_path("empty.txt")}')
            two = self.fetch_async('/html_source_dmp?format=json&'
                                   f'a=file://{fixture_path("empty.txt")}&'
                                   f'b=file://{fixture_path("empty.txt")}')
            response1, response2 = await asyncio.gather(one, two)
            assert response1.code == 200
            assert response2.code == 200
            assert executor_resets == 1
            # Ensure *both* diffs hit the bad executor, so we know we didn't
            # have one reset because only one request hit the bad executor.
            assert bad_executor.submit_count == 2

    # NOTE: the real `quit` tears up the server, which causes porblems with
    # the test, so we just mock it and test that it was called, rather than
    # checking whether `sys.exit` was ultimately called. Not totally ideal,
    # but better than no testing.
    @patch.object(df.DiffServer, "quit")
    def test_server_exits_if_process_pool_repeatedly_breaks(self, mock_quit):
        # Set a custom executor that will always fail.
        def get_executor(self, reset=False):
            return BrokenProcessPoolExecutor()

        with patch.object(df.DiffHandler, 'get_diff_executor', get_executor):
            response = self.fetch('/html_source_dmp?format=json&'
                                  f'a=file://{fixture_path("empty.txt")}&'
                                  f'b=file://{fixture_path("empty.txt")}')
            self.json_check(response)
            assert response.code == 500

        assert mock_quit.called
        assert mock_quit.call_args == ({'code': 10},)

    # NOTE: the real `quit` tears up the server, which causes porblems with
    # the test, so we just mock it and test that it was called, rather than
    # checking whether `sys.exit` was ultimately called. Not totally ideal,
    # but better than no testing.
    @patch.object(df.DiffServer, "quit")
    @patch('web_monitoring_diff.server.server.RESTART_BROKEN_DIFFER', True)
    def test_server_does_not_exit_if_env_var_set_when_process_pool_repeatedly_breaks(self, mock_quit):
        # Set a custom executor that will always fail.
        def get_executor(self, reset=False):
            return BrokenProcessPoolExecutor()

        with patch.object(df.DiffHandler, 'get_diff_executor', get_executor):
            response = self.fetch('/html_source_dmp?format=json&'
                                  f'a=file://{fixture_path("empty.txt")}&'
                                  f'b=file://{fixture_path("empty.txt")}')
            self.json_check(response)
            assert response.code == 500

        assert not mock_quit.called


def mock_diffing_method(c_body):
    return


def fixture_path(fixture):
    return Path(__file__).resolve().parent / 'fixtures' / fixture


# TODO: merge this functionality in with MockAsyncHttpClient? It could have the
# ability to serve a [fixture] file.
def mock_tornado_request(fixture, headers=None):
    path = fixture_path(fixture)
    with open(path, 'rb') as f:
        body = f.read()
        return df.MockResponse(f'file://{path}', body, headers)


# TODO: we may want to extract this to a support module
class MockAsyncHttpClient(AsyncHTTPClient):
    """
    A mock Tornado AsyncHTTPClient. Use it to set fake responses and track
    requests made with an AsyncHTTPClient instance.
    """

    def __init__(self):
        self.requests = {}
        self.stub_responses = []

    def respond_to(self, matcher, code=200, body='', headers={}, **kwargs):
        """
        Set up a fake HTTP response. If a request is made and no fake response
        set up with `respond_to()` matches it, an error will be raised.

        Parameters
        ----------
        matcher : callable or string
            Defines whether this response data should be used for a given
            request. If callable, it will be called with the Tornado Request
            object and should return `True` if the response should be used. If
            a string, it will be used as a regular expression to match the
            request URL.
        code : int, default: 200
            The HTTP response code to response with.
        body : string, optional
            The response body to send back.
        headers : dict, optional
            Any headers to use for the response.
        **kwargs : any, optional
            Additional keyword args to pass to the Tornado Response.
            Reference: http://www.tornadoweb.org/en/stable/httpclient.html#tornado.httpclient.HTTPResponse
        """
        if isinstance(matcher, str):
            regex = re.compile(matcher)
            matcher = lambda request: regex.search(request.url) is not None

        if 'Content-Type' not in headers and 'content-type' not in headers:
            headers['Content-Type'] = 'text/plain'

        self.stub_responses.append({
            'matcher': matcher,
            'code': code,
            'body': body,
            'headers': headers,
            'extra': kwargs
        })

    def fetch_impl(self, request, callback):
        stub = self._find_stub(request)
        buffer = BytesIO(utf8(stub['body']))
        headers = HTTPHeaders(stub['headers'])
        response = HTTPResponse(request, stub['code'], buffer=buffer,
                                headers=headers, **stub['extra'])
        self.requests[request.url] = request
        callback(response)

    def _find_stub(self, request):
        for stub in self.stub_responses:
            if stub['matcher'](request):
                return stub
        raise ValueError(f'No response stub for {request.url}')


class MockResponderHeadersTest(unittest.TestCase):
    def test_pdf_extension(self):
        response = df.MockResponse(f'file://{fixture_path("simple.pdf")}', '')
        assert response.headers['Content-Type'] == 'application/pdf'

    def test_html_extension(self):
        response = df.MockResponse(f'file://{fixture_path("unknown_encoding.html")}', '')
        assert response.headers['Content-Type'] == 'text/html'

    def test_txt_extension(self):
        response = df.MockResponse(f'file://{fixture_path("empty.txt")}', '')
        assert response.headers['Content-Type'] == 'text/plain'

    def test_no_extension_should_assume_html(self):
        response = df.MockResponse(f'file://{fixture_path("unknown_encoding")}', '')
        assert response.headers['Content-Type'] == 'text/html'

    def test_unknown_extension_should_assume_html(self):
        response = df.MockResponse(f'file://{fixture_path("unknown_encoding.notarealextension")}', '')
        assert response.headers['Content-Type'] == 'text/html'


class SimpleHttpServer:
    """
    Based on the internals of Tornado's AsyncHTTPTestCase. This is a simple
    Tornado web server that just runs the provided callable for any request.

    Parameters
    ----------
    handler : callable
        Called whenever a request is made to the server. The first parameter
        will be a Tornado `RequestHandler` instance.
    """
    def __init__(self, handler):
        sock, port = bind_unused_port()
        self._port = port
        self._app = tornado.web.Application([(r".*", SimpleHttpServerHandler)],
                                            debug=True,
                                            actual_handler=handler)
        self.http_server = HTTPServer(self._app)
        self.http_server.add_sockets([sock])

    def stop(self):
        self.http_server.stop()

    def url(self, path):
        return f'http://localhost:{self._port}{path}'

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.stop()


class SimpleHttpServerHandler(tornado.web.RequestHandler):
    async def get(self):
        handler = self.settings.get('actual_handler')
        await handler(self)
