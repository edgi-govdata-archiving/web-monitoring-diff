import pycurl
from tornado.curl_httpclient import CurlAsyncHTTPClient


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