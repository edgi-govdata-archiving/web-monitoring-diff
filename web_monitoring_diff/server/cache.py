"""
Caching support for the web-monitoring-diff server.

Provides two separate caches: a response cache for storing raw HTTP
response bodies, and a diff cache for storing serialized diff results.

Both caches use dogpile.cache as the interface layer.

Environment Variables
---------------------
CACHE_REDIS_URL : str
    Full Redis connection URL, e.g. redis://localhost:6379/0.
    When set, Redis is used as the cache backend.
    If not set, an in-memory cache is used instead.

CACHE_RESPONSE_EXPIRATION_SECONDS : int
    How long a cached HTTP response is considered fresh, in seconds.
    Defaults to 3600.

CACHE_DIFF_EXPIRATION_SECONDS : int
    How long a cached diff result is considered fresh, in seconds.
    Defaults to 86400. Set to 0 to disable diff result caching.

CACHE_MAX_SIZE : int
    Maximum number of items stored in the in-memory backend.
    Defaults to 500.
"""

import hashlib
import json
import logging
import os
import pickle

logger = logging.getLogger(__name__)

# Configuration read from environment
CACHE_REDIS_URL = os.environ.get('CACHE_REDIS_URL', '')
CACHE_RESPONSE_EXPIRATION_SECONDS = int(
    os.environ.get('CACHE_RESPONSE_EXPIRATION_SECONDS', 3600))
CACHE_DIFF_EXPIRATION_SECONDS = int(
    os.environ.get('CACHE_DIFF_EXPIRATION_SECONDS', 86400))
CACHE_MAX_SIZE = int(os.environ.get('CACHE_MAX_SIZE', 500))


def _require_dogpile():
    """
    Ensure the dogpile cache library is installed before proceeding.
    """
    try:
        from dogpile.cache import make_region
        return make_region
    except ImportError as exc:
        raise ImportError(
            'dogpile.cache is required for server-side caching. '
            'Install it with: pip install "web-monitoring-diff[cache]"'
        ) from exc


class CachedResponse:
    """
    Lightweight container holding an HTTP response for diffing.
    
    Parameters
    ----------
    url : str
        The URL of the response.
    body : bytes
        The response body content.
    headers : dict
        A dictionary of HTTP headers.
    """
    __slots__ = ('url', 'body', 'headers')

    def __init__(self, url, body, headers):
        self.url = url
        self.body = body
        # Store headers as a plain dict so pickle round-trips cleanly.
        self.headers = dict(headers) if headers else {}

    # Duck-typing support to act like a Tornado HTTPResponse or MockResponse.
    class _FakeRequest:
        __slots__ = ('url',)
        def __init__(self, url):
            self.url = url

    @property
    def request(self):
        return self._FakeRequest(self.url)


def _serialize(value):
    """Serialize a value to bytes for storage."""
    return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)


def _deserialize(data):
    """
    Deserialize a value from bytes.
    """
    if data is None:
        return None
    return pickle.loads(data)


def _make_redis_region(name, expiration_seconds):
    """Build a dogpile.cache region backed by Redis."""
    make_region = _require_dogpile()

    backend_args = {'url': CACHE_REDIS_URL}

    # Use hiredis if available for improved parsing performance.
    try:
        import hiredis  # noqa: F401
        logger.debug('hiredis is available; Redis parsing will be accelerated.')
    except ImportError:
        logger.debug('hiredis not found; using pure-Python Redis parser.')

    region = make_region(name=name).configure(
        'dogpile.cache.redis',
        expiration_time=expiration_seconds,
        arguments=backend_args,
    )
    logger.info(
        'Cache "%s" using Redis backend at %s (TTL %ds)',
        name, CACHE_REDIS_URL, expiration_seconds,
    )
    return region


def _make_memory_region(name, expiration_seconds):
    """Build a dogpile.cache region backed by a simple in-memory dict."""
    make_region = _require_dogpile()
    region = make_region(name=name).configure(
        'dogpile.cache.memory',
        expiration_time=expiration_seconds,
        arguments={'cache_size': CACHE_MAX_SIZE},
    )
    logger.info(
        'Cache "%s" using in-memory backend (max size %d, TTL %ds)',
        name, CACHE_MAX_SIZE, expiration_seconds,
    )
    return region


def _make_region(name, expiration_seconds):
    """Return an appropriate dogpile.cache region for the current config."""
    if CACHE_REDIS_URL:
        try:
            return _make_redis_region(name, expiration_seconds)
        except Exception as exc:
            logger.warning(
                'Failed to connect to Redis (%s). '
                'Falling back to in-memory cache for "%s".',
                exc, name,
            )
    return _make_memory_region(name, expiration_seconds)


# Module-level singletons; created lazily so that import doesn't fail when
# dogpile.cache is not installed.
_response_region = None
_diff_region = None


def _get_response_region():
    """Retrieve the global response cache region, creating it if necessary."""
    global _response_region
    if _response_region is None:
        _response_region = _make_region(
            'responses', CACHE_RESPONSE_EXPIRATION_SECONDS)
    return _response_region


def _get_diff_region():
    """Retrieve the global diff cache region, creating it if necessary."""
    global _diff_region
    if _diff_region is None:
        _diff_region = _make_region(
            'diffs', CACHE_DIFF_EXPIRATION_SECONDS)
    return _diff_region


def make_response_cache_key(url):
    """
    Create a stable cache key for a fetched HTTP response.
    
    Parameters
    ----------
    url : str
        The URL that was fetched.
        
    Returns
    -------
    str
        A unique string key using the SHA-256 hex digest of the URL.
    """
    return 'response:' + hashlib.sha256(url.encode()).hexdigest()


def make_diff_cache_key(differ_name, a_url, b_url, extra_params):
    """
    Create a stable cache key for a diff result.
    
    Parameters
    ----------
    differ_name : str
        The name of the type of diff performed.
    a_url : str
        Target URL a.
    b_url : str
        Target URL b.
    extra_params : dict
        Additional query parameters.
        
    Returns
    -------
    str
        A unique string key using the SHA-256 hex digest of the inputs.
    """
    params_repr = json.dumps(extra_params, sort_keys=True)
    key_data = f'{differ_name}\x00{a_url}\x00{b_url}\x00{params_repr}'
    return 'diff:' + hashlib.sha256(key_data.encode()).hexdigest()


def get_cached_response(url):
    """
    Retrieve a cached response for the given URL.
    
    Parameters
    ----------
    url : str
        The URL to check the cache for.
        
    Returns
    -------
    CachedResponse or None
        The cached HTTP response, or None if not found or if caching is disabled.
    """
    try:
        region = _get_response_region()
        key = make_response_cache_key(url)
        raw = region.get(key)
        if raw is None:
            return None
        # dogpile.cache uses a sentinel NO_VALUE object; check for it.
        from dogpile.cache.api import NO_VALUE
        if raw is NO_VALUE:
            return None
        value = _deserialize(raw)
        logger.debug('Response cache HIT for %s', url)
        return value
    except Exception:
        logger.exception('Error reading from response cache for %s', url)
        return None


def cache_response(url, response):
    """
    Store an HTTP response in the cache for a given URL.
    
    Errors are silently ignored so that cache failures never break a request.
    
    Parameters
    ----------
    url : str
        The URL corresponding to the response.
    response : HTTPResponse or MockResponse
        The response object to serialize and cache.
    """
    try:
        region = _get_response_region()
        key = make_response_cache_key(url)
        cached = CachedResponse(
            url=url,
            body=response.body,
            headers=response.headers,
        )
        region.set(key, _serialize(cached))
        logger.debug('Response cache SET for %s', url)
    except Exception:
        logger.exception('Error writing to response cache for %s', url)


def get_cached_diff(differ_name, a_url, b_url, extra_params):
    """
    Retrieve a cached diff result for the given inputs.
    
    Parameters
    ----------
    differ_name : str
        The name of the type of diff performed.
    a_url : str
        Target URL a.
    b_url : str
        Target URL b.
    extra_params : dict
        Additional query parameters.
        
    Returns
    -------
    dict or None
        The cached JSON-serializable diff payload, or None if not found.
    """
    if CACHE_DIFF_EXPIRATION_SECONDS == 0:
        return None
    try:
        region = _get_diff_region()
        key = make_diff_cache_key(differ_name, a_url, b_url, extra_params)
        raw = region.get(key)
        if raw is None:
            return None
        from dogpile.cache.api import NO_VALUE
        if raw is NO_VALUE:
            return None
        value = _deserialize(raw)
        logger.debug('Diff cache HIT for %s(%s, %s)', differ_name, a_url, b_url)
        return value
    except Exception:
        logger.exception('Error reading from diff cache')
        return None


def cache_diff(differ_name, a_url, b_url, extra_params, result):
    """
    Store a computed diff result in the cache.
    
    Errors are silently ignored.
    
    Parameters
    ----------
    differ_name : str
        The name of the type of diff performed.
    a_url : str
        Target URL a.
    b_url : str
        Target URL b.
    extra_params : dict
        Additional query parameters.
    result : dict
        The JSON-serializable payload produced by the differ function.
    """
    if CACHE_DIFF_EXPIRATION_SECONDS == 0:
        return
    try:
        region = _get_diff_region()
        key = make_diff_cache_key(differ_name, a_url, b_url, extra_params)
        region.set(key, _serialize(result))
        logger.debug('Diff cache SET for %s(%s, %s)', differ_name, a_url, b_url)
    except Exception:
        logger.exception('Error writing to diff cache')


def is_caching_enabled():
    """
    Check if the caching library dependency is met.
    
    Returns
    -------
    bool
        True when dogpile.cache is globally importable.
    """
    try:
        import dogpile.cache  # noqa: F401
        return True
    except ImportError:
        return False
