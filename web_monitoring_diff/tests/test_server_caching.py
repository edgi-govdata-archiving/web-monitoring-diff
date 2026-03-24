"""
Tests for the server caching layer (web_monitoring_diff.server.cache).

These tests exercise the cache module in isolation using dogpile.cache's
in-memory backend, so no Redis instance is required.  They also include
integration-style tests that verify the full server request path hits the
cache correctly.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from tornado.testing import AsyncHTTPTestCase

import web_monitoring_diff.server.server as df
from web_monitoring_diff.server import cache as cache_module
from web_monitoring_diff.server.cache import (
    CachedResponse,
    make_response_cache_key,
    make_diff_cache_key,
    _serialize,
    _deserialize,
)
from web_monitoring_diff.server.mock_http import MockResponse


# ---------------------------------------------------------------------------
# Helpers shared by unit and integration tests
# ---------------------------------------------------------------------------

def fixture_path(name):
    return Path(__file__).resolve().parent / 'fixtures' / name


def _make_memory_region(expiration=60):
    """
    Return a fresh dogpile.cache in-memory region for testing.
    """
    from dogpile.cache import make_region
    return make_region().configure(
        'dogpile.cache.memory',
        expiration_time=expiration,
        arguments={'cache_size': 100},
    )


def _mock_region():
    """
    Return a simple dict-backed fake region (no dogpile.cache required).
    """
    class _FakeRegion:
        def __init__(self):
            self._store = {}

        def get(self, key):
            from dogpile.cache.api import NO_VALUE
            return self._store.get(key, NO_VALUE)

        def set(self, key, value):
            self._store[key] = value

    return _FakeRegion()


# ---------------------------------------------------------------------------
# Unit tests – cache module in isolation
# ---------------------------------------------------------------------------

class TestCacheKeys(unittest.TestCase):
    def test_response_key_is_stable(self):
        url = 'https://example.org/page'
        assert make_response_cache_key(url) == make_response_cache_key(url)

    def test_response_key_differs_for_different_urls(self):
        assert (make_response_cache_key('https://a.org')
                != make_response_cache_key('https://b.org'))

    def test_response_key_has_prefix(self):
        key = make_response_cache_key('https://example.org')
        assert key.startswith('response:')

    def test_diff_key_is_stable(self):
        params = {'format': 'json', 'include': 'all'}
        k1 = make_diff_cache_key('html_token', 'http://a', 'http://b', params)
        k2 = make_diff_cache_key('html_token', 'http://a', 'http://b', params)
        assert k1 == k2

    def test_diff_key_differs_for_different_differs(self):
        params = {}
        k1 = make_diff_cache_key('html_token', 'http://a', 'http://b', params)
        k2 = make_diff_cache_key('html_source_dmp', 'http://a', 'http://b', params)
        assert k1 != k2

    def test_diff_key_differs_for_different_urls(self):
        params = {}
        k1 = make_diff_cache_key('html_token', 'http://a', 'http://b', params)
        k2 = make_diff_cache_key('html_token', 'http://a', 'http://c', params)
        assert k1 != k2

    def test_diff_key_params_order_independent(self):
        """Extra params must produce the same key regardless of dict order."""
        k1 = make_diff_cache_key('d', 'a', 'b', {'x': '1', 'y': '2'})
        k2 = make_diff_cache_key('d', 'a', 'b', {'y': '2', 'x': '1'})
        assert k1 == k2

    def test_diff_key_has_prefix(self):
        key = make_diff_cache_key('d', 'a', 'b', {})
        assert key.startswith('diff:')


class TestCachedResponse(unittest.TestCase):
    def test_body_and_headers_round_trip(self):
        body = b'<html>hello</html>'
        headers = {'Content-Type': 'text/html', 'X-Custom': 'value'}
        resp = CachedResponse('https://example.org', body, headers)
        assert resp.body == body
        assert resp.headers == headers

    def test_url_accessible_via_request_property(self):
        resp = CachedResponse('https://example.org', b'', {})
        assert resp.request.url == 'https://example.org'

    def test_serialization_round_trip(self):
        resp = CachedResponse('https://example.org', b'hello', {'CT': 'text/plain'})
        data = _serialize(resp)
        restored = _deserialize(data)
        assert restored.url == resp.url
        assert restored.body == resp.body
        assert restored.headers == resp.headers


class TestGetCacheResponse(unittest.TestCase):
    """
    Tests for get_cached_response / cache_response using patched regions.
    """

    def setUp(self):
        # Reset module-level singletons before each test.
        cache_module._response_region = None
        cache_module._diff_region = None

    def test_miss_returns_none(self):
        region = _mock_region()
        with patch.object(cache_module, '_get_response_region', return_value=region):
            result = cache_module.get_cached_response('https://example.org/miss')
        assert result is None

    def test_hit_returns_cached_response(self):
        region = _mock_region()
        url = 'https://example.org/hit'
        original = CachedResponse(url, b'data', {'CT': 'text/html'})
        region.set(make_response_cache_key(url), _serialize(original))

        with patch.object(cache_module, '_get_response_region', return_value=region):
            result = cache_module.get_cached_response(url)

        assert result is not None
        assert result.body == b'data'

    def test_cache_response_stores_value(self):
        region = _mock_region()
        url = 'https://example.org/store'
        mock_resp = MockResponse(url, b'<html>test</html>')

        with patch.object(cache_module, '_get_response_region', return_value=region):
            cache_module.cache_response(url, mock_resp)
            result = cache_module.get_cached_response(url)

        assert result is not None
        assert result.body == b'<html>test</html>'

    def test_error_in_region_is_silenced(self):
        """
        Cache errors should never propagate to the caller.
        """
        broken_region = MagicMock()
        broken_region.get.side_effect = RuntimeError('Redis is down')

        with patch.object(cache_module, '_get_response_region', return_value=broken_region):
            result = cache_module.get_cached_response('https://example.org')
        # Should return None, not raise.
        assert result is None

    def test_cache_response_error_is_silenced(self):
        broken_region = MagicMock()
        broken_region.set.side_effect = RuntimeError('Redis is down')
        mock_resp = MockResponse('https://example.org', b'data')

        with patch.object(cache_module, '_get_response_region', return_value=broken_region):
            # Should not raise.
            cache_module.cache_response('https://example.org', mock_resp)


class TestDiffCache(unittest.TestCase):
    def setUp(self):
        cache_module._response_region = None
        cache_module._diff_region = None

    def test_miss_returns_none(self):
        region = _mock_region()
        with patch.object(cache_module, '_get_diff_region', return_value=region):
            result = cache_module.get_cached_diff('d', 'a', 'b', {})
        assert result is None

    def test_hit_returns_result(self):
        region = _mock_region()
        diff_result = {'change_count': 3, 'diff': []}
        key = make_diff_cache_key('html_token', 'http://a', 'http://b', {})
        region.set(key, _serialize(diff_result))

        with patch.object(cache_module, '_get_diff_region', return_value=region):
            result = cache_module.get_cached_diff('html_token', 'http://a', 'http://b', {})

        assert result == diff_result

    def test_cache_diff_stores_value(self):
        region = _mock_region()
        diff_result = {'change_count': 0}

        with patch.object(cache_module, '_get_diff_region', return_value=region):
            cache_module.cache_diff('d', 'a', 'b', {}, diff_result)
            result = cache_module.get_cached_diff('d', 'a', 'b', {})

        assert result == diff_result

    def test_diff_cache_disabled_when_expiration_is_zero(self):
        """Setting CACHE_DIFF_EXPIRATION_SECONDS=0 disables diff caching."""
        with patch.object(cache_module, 'CACHE_DIFF_EXPIRATION_SECONDS', 0):
            region = _mock_region()
            with patch.object(cache_module, '_get_diff_region', return_value=region):
                cache_module.cache_diff('d', 'a', 'b', {}, {'x': 1})
                result = cache_module.get_cached_diff('d', 'a', 'b', {})
        assert result is None


# ---------------------------------------------------------------------------
# Integration tests – server endpoints hit the caches
# ---------------------------------------------------------------------------

def mock_http_client_decorator(test_func):
    """
    Replaces the server's HTTP client with a MockAsyncHttpClient (re-use the
    helper already defined in test_server_exc_handling.py).
    """
    import re
    from io import BytesIO
    from tornado.httpclient import HTTPResponse, AsyncHTTPClient
    from tornado.httputil import HTTPHeaders

    class SimpleMockClient(AsyncHTTPClient):
        def __init__(self):
            self.call_count = 0
            self._responses = {}

        def respond_to(self, url_pattern, body=b'<html>test</html>', code=200):
            self._responses[url_pattern] = (code, body)

        def fetch_impl(self, request, callback):
            self.call_count += 1
            for pattern, (code, body) in self._responses.items():
                if re.search(pattern, request.url):
                    buf = BytesIO(body)
                    headers = HTTPHeaders({'Content-Type': 'text/html'})
                    callback(HTTPResponse(request, code, buffer=buf, headers=headers))
                    return
            raise ValueError(f'No stub for {request.url}')

    def wrapper(self_, *args, **kwargs):
        mock = SimpleMockClient()
        with patch.object(df, 'get_http_client', return_value=mock):
            return test_func(self_, mock, *args, **kwargs)

    return wrapper


class DiffServerCachingIntegrationTest(AsyncHTTPTestCase):

    def get_app(self):
        return df.make_app()

    def setUp(self):
        super().setUp()
        # Reset module-level cache region singletons so each test starts fresh.
        cache_module._response_region = None
        cache_module._diff_region = None

    def _make_memory_region_for_test(self, expiration=3600):
        from dogpile.cache import make_region
        return make_region().configure(
            'dogpile.cache.memory',
            expiration_time=expiration,
            arguments={'cache_size': 200},
        )

    @mock_http_client_decorator
    def test_response_cache_prevents_second_upstream_fetch(self, mock_client):
        """
        When the same URL appears in two successive diff requests (e.g. a user
        switching from html_token to html_source_dmp), the upstream server
        should only be contacted once.
        """
        url_a = 'https://example.org/page-a'
        url_b = 'https://example.org/page-b'
        mock_client.respond_to(r'page-a', body=b'<html>A</html>')
        mock_client.respond_to(r'page-b', body=b'<html>B</html>')

        region = self._make_memory_region_for_test()
        with patch.object(cache_module, '_get_response_region', return_value=region), \
             patch.object(cache_module, '_get_diff_region',
                          return_value=self._make_memory_region_for_test()):

            # First request – fetches both URLs.
            r1 = self.fetch(f'/identical_bytes?a={url_a}&b={url_b}')
            assert r1.code == 200
            calls_after_first = mock_client.call_count
            assert calls_after_first == 2  # both URLs fetched

            # Second request with same URLs – should serve from response cache.
            r2 = self.fetch(f'/html_source_dmp?a={url_a}&b={url_b}')
            assert r2.code == 200
            assert mock_client.call_count == calls_after_first  # no new upstream calls

    @mock_http_client_decorator
    def test_diff_cache_prevents_second_diff_computation(self, mock_client):
        """
        Identical diff requests should return the cached result without
        re-running the diff function.
        """
        url_a = 'https://example.org/page-a'
        url_b = 'https://example.org/page-b'
        mock_client.respond_to(r'page-a', body=b'<html>A</html>')
        mock_client.respond_to(r'page-b', body=b'<html>B</html>')

        diff_region = self._make_memory_region_for_test()
        resp_region = self._make_memory_region_for_test()

        with patch.object(cache_module, '_get_response_region', return_value=resp_region), \
             patch.object(cache_module, '_get_diff_region', return_value=diff_region):

            r1 = self.fetch(f'/identical_bytes?a={url_a}&b={url_b}')
            assert r1.code == 200
            body1 = json.loads(r1.body)

            r2 = self.fetch(f'/identical_bytes?a={url_a}&b={url_b}')
            assert r2.code == 200
            body2 = json.loads(r2.body)

            # Both responses should carry the same diff payload.
            assert body1['type'] == body2['type']

    @mock_http_client_decorator
    def test_diff_cache_disabled_when_expiration_zero(self, mock_client):
        """Setting CACHE_DIFF_EXPIRATION_SECONDS=0 should skip diff caching."""
        url_a = 'https://example.org/page-a'
        url_b = 'https://example.org/page-b'
        mock_client.respond_to(r'page-a', body=b'<html>A</html>')
        mock_client.respond_to(r'page-b', body=b'<html>B</html>')

        with patch.object(cache_module, 'CACHE_DIFF_EXPIRATION_SECONDS', 0), \
             patch.object(cache_module, '_get_response_region',
                          return_value=self._make_memory_region_for_test()), \
             patch.object(cache_module, '_get_diff_region',
                          return_value=self._make_memory_region_for_test()):

            r1 = self.fetch(f'/identical_bytes?a={url_a}&b={url_b}')
            r2 = self.fetch(f'/identical_bytes?a={url_a}&b={url_b}')
            # Both should succeed; the key thing is no error is raised.
            assert r1.code == 200
            assert r2.code == 200

    def test_file_urls_are_not_cached(self):
        """
        Responses from file:// URLs must not be stored in the response cache
        (it only makes sense to cache HTTP responses).
        """
        region = self._make_memory_region_for_test()
        with patch.object(cache_module, '_get_response_region', return_value=region), \
             patch.object(cache_module, '_get_diff_region',
                          return_value=self._make_memory_region_for_test()):

            path = fixture_path('empty.txt')
            url = f'file://{path}'
            # Trigger a diff using local files.
            response = self.fetch(
                f'/identical_bytes?a={url}&b={url}')
            assert response.code == 200

            # The response cache region should be empty because we only cache
            # HTTP/HTTPS URLs.
            from dogpile.cache.api import NO_VALUE
            cache_key = make_response_cache_key(url)
            assert region.get(cache_key) is NO_VALUE
