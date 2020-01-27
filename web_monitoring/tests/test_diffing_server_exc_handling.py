import json
import os
import unittest
from pathlib import Path
import re
import tempfile
from tornado.testing import AsyncHTTPTestCase
from unittest.mock import patch
import web_monitoring.diff_server.server as df
from web_monitoring.diff.diff_errors import UndecodableContentError
import web_monitoring
from tornado.escape import utf8
from tornado.httpclient import HTTPResponse, AsyncHTTPClient
from tornado.httputil import HTTPHeaders
from io import BytesIO


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
        assert json_response['version'] == web_monitoring.__version__


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
        with patch.object(df, 'client', wraps=mock):
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
        with patch.object(df, 'client', wraps=mock):
            mock.respond_to(r'/error$', code=404, headers={'Memento-Datetime': 'Tue Sep 25 2018 03:38:50'})
            mock.respond_to(r'/success$')

            response = self.fetch('/html_token?format=json&include=all'
                                  '&a=https://archive.org/20180925033850/http://httpstat.us/error'
                                  '&b=https://example.org/success')

            self.assertEqual(response.code, 200)
            assert 'change_count' in json.loads(response.body)

    @patch('web_monitoring.diff_server.server.access_control_allow_origin_header', '*')
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

    @patch('web_monitoring.diff_server.server.access_control_allow_origin_header',
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
        code : int, optional
            The HTTP response code to response with. Defaults to 200 (OK).
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
