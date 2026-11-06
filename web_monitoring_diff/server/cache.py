"""
Caching support for the web-monitoring-diff server.

Two separate caches are provided:

* A response cache that stores the raw HTTP response body and headers
  for a given URL. This avoids repeated upstream fetches when the same
  URL appears across successive diff requests (e.g. a user switching
  between differ types, or comparing capture X→Y then X→Z).

* A diff cache that stores the serialized result of a completed diff
  keyed on the differ name, both URLs, and any extra query parameters.

Both caches use dogpile.cache as the underlying interface, which
supports pluggable backends.  Redis is used when ``CACHE_REDIS_URL``
is set in the environment; otherwise a simple in-memory dict is used
as a fallback.

Environment variables
---------------------
CACHE_REDIS_URL
    Full Redis connection URL, e.g. ``redis://localhost:6379/0``.
    When not set, an in-memory cache is used instead.
CACHE_RESPONSE_EXPIRATION_SECONDS
    TTL for cached HTTP responses, in seconds (default: 3600).
CACHE_DIFF_EXPIRATION_SECONDS
    TTL for cached diff results, in seconds (default: 86400).
    Set to 0 to disable diff result caching entirely.
CACHE_MAX_SIZE
    Maximum number of entries in the in-memory backend (default: 500).
    Ignored when Redis is used.
"""

import hashlib
import json
import logging
import os
import pickle

logger = logging.getLogger(__name__)

CACHE_REDIS_URL = os.environ.get('CACHE_REDIS_URL', '')
CACHE_RESPONSE_EXPIRATION_SECONDS = int(os.environ.get('CACHE_RESPONSE_EXPIRATION_SECONDS', 3600))
CACHE_DIFF_EXPIRATION_SECONDS = int(os.environ.get('CACHE_DIFF_EXPIRATION_SECONDS', 86400))
CACHE_MAX_SIZE = int(os.environ.get('CACHE_MAX_SIZE', 500))


def _require_dogpile():
    try:
        from dogpile.cache import make_region
        return make_region
    except ImportError as exc:
        raise ImportError(
            'dogpile.cache is required for server-side caching. '
            'Install it with: pip install "web-monitoring-diff[cache]"'
        ) from exc


class CachedResponse:
    """A lightweight stand-in for a Tornado HTTPResponse.

    Stores only the parts of a response that the server actually needs for
    diffing (URL, body, headers) and exposes the same ``.request.url``
    attribute that the rest of the server code depends on.
    """
    __slots__ = ('url', 'body', 'headers')

    def __init__(self, url, body, headers):
        self.url = url
        self.body = body
        # Store headers as a plain dict so pickle round-trips cleanly.
        self.headers = dict(headers) if headers else {}

    class _FakeRequest:
        __slots__ = ('url',)
        def __init__(self, url):
            self.url = url

    @property
    def request(self):
        return self._FakeRequest(self.url)


def _serialize(value):
    return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)


def _deserialize(data):
    return pickle.loads(data) if data is not None else None


def _make_region(name, expiration_seconds):
    """Configure and return a dogpile.cache region.

    Tries Redis first (when ``CACHE_REDIS_URL`` is set), and falls back to
    the in-memory backend if the connection fails.
    """
    make_region = _require_dogpile()

    if CACHE_REDIS_URL:
        try:
            # redis-py automatically uses hiredis when it is importable, giving
            # a significant parsing speedup with no extra configuration needed.
            try:
                import hiredis  # noqa: F401
                logger.debug('hiredis is available; Redis parsing will be accelerated.')
            except ImportError:
                logger.debug('hiredis not found; using pure-Python Redis parser.')

            region = make_region(name=name).configure(
                'dogpile.cache.redis',
                expiration_time=expiration_seconds,
                arguments={'url': CACHE_REDIS_URL},
            )
            logger.info('Cache "%s" using Redis backend at %s (TTL %ds)',
                        name, CACHE_REDIS_URL, expiration_seconds)
            return region
        except Exception as exc:
            logger.warning('Failed to connect to Redis (%s); falling back to in-memory cache for "%s".',
                           exc, name)

    region = make_region(name=name).configure(
        'dogpile.cache.memory',
        expiration_time=expiration_seconds,
        arguments={'cache_size': CACHE_MAX_SIZE},
    )
    logger.info('Cache "%s" using in-memory backend (max size %d, TTL %ds)',
                name, CACHE_MAX_SIZE, expiration_seconds)
    return region


# Lazily-created singletons so importing this module doesn't fail when
# dogpile.cache isn't installed.
_regions = {}


def _get_region(name, expiration_seconds):
    if name not in _regions:
        _regions[name] = _make_region(name, expiration_seconds)
    return _regions[name]


# Thin wrappers used as interception points in tests.
def _get_response_region():
    return _get_region('responses', CACHE_RESPONSE_EXPIRATION_SECONDS)


def _get_diff_region():
    return _get_region('diffs', CACHE_DIFF_EXPIRATION_SECONDS)


def _cache_get(region_getter, key, label):
    """Try to read *key* from the region returned by *region_getter*.

    Returns the deserialized value on a hit, or ``None`` on a miss or error.
    Cache errors are always logged and swallowed so they never break a request.
    """
    try:
        raw = region_getter().get(key)
        if raw is None:
            return None
        from dogpile.cache.api import NO_VALUE
        if raw is NO_VALUE:
            return None
        logger.debug('%s cache HIT for %s', label, key)
        return _deserialize(raw)
    except Exception:
        logger.exception('Error reading from %s cache', label.lower())
        return None


def _cache_set(region_getter, key, value, label):
    """Try to write *value* to the region returned by *region_getter*.

    Errors are logged and swallowed so they never break a request.
    """
    try:
        region_getter().set(key, _serialize(value))
        logger.debug('%s cache SET for %s', label, key)
    except Exception:
        logger.exception('Error writing to %s cache', label.lower())


# Public API
def make_response_cache_key(url):
    "Return the cache key for a given URL's response."
    return 'response:' + hashlib.sha256(url.encode()).hexdigest()


def make_diff_cache_key(differ_name, a_url, b_url, extra_params):
    "Return the cache key for a given diff configuration."
    params_repr = json.dumps(extra_params, sort_keys=True)
    key_data = f'{differ_name}\x00{a_url}\x00{b_url}\x00{params_repr}'
    return 'diff:' + hashlib.sha256(key_data.encode()).hexdigest()


def get_cached_response(url):
    "Return a cached CachedResponse for *url*, or None on a miss."
    return _cache_get(_get_response_region, make_response_cache_key(url), 'Response')


def cache_response(url, response):
    "Store an HTTP response body and headers in the response cache."
    cached = CachedResponse(url=url, body=response.body, headers=response.headers)
    _cache_set(_get_response_region, make_response_cache_key(url), cached, 'Response')


def get_cached_diff(differ_name, a_url, b_url, extra_params):
    "Return a cached diff result dict for the given inputs, or None on a miss."
    if CACHE_DIFF_EXPIRATION_SECONDS == 0:
        return None
    return _cache_get(_get_diff_region,
                      make_diff_cache_key(differ_name, a_url, b_url, extra_params),
                      'Diff')


def cache_diff(differ_name, a_url, b_url, extra_params, result):
    "Store a diff result dict in the diff cache."
    if CACHE_DIFF_EXPIRATION_SECONDS == 0:
        return
    _cache_set(_get_diff_region,
               make_diff_cache_key(differ_name, a_url, b_url, extra_params),
               result, 'Diff')


def is_caching_enabled():
    "Return True if dogpile.cache is installed."
    try:
        import dogpile.cache  # noqa: F401
        return True
    except ImportError:
        return False
