import mimetypes

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
