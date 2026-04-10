import tornado.web


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
    def __init__(self, status_code=500, public_message=None, log_message=None,
                 extra=None, **kwargs):
        self.extra = extra or {}

        if public_message is not None:
            if 'error' not in self.extra:
                self.extra['error'] = public_message

            if log_message is None:
                log_message = public_message

        super().__init__(status_code, log_message, **kwargs)