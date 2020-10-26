=============
API Reference
=============


Diff Types
----------

*web-monitoring-diff* provides a variety of diff algorithms for use in comparing web content. They all follow a similar standardized signature and return format.

**Diff Signatures**

All diffs should have parameters named ``a_<body|text>`` and ``b_<body|text>`` as their first two arguments. These represent the two pieces of content to compare, where ``a`` represents the “from” or left-hand side and ``b`` represents the “to” or right-hand side of the comparison. The name indicates whether the function takes bytes (``a_body``/``b_body``) or a decoded string (``a_text``/``b_text``). The web server inspects argument names to determine what to pass to a given diff type.

Additionally, diffs may take several other standardized parameters:

* ``a_body``, ``b_body``: Raw HTTP reponse body (bytes), described above.
* ``a_text``, ``b_text``: Decoded text of HTTP response body (str), described above.
* ``a_url``, ``b_url``: URL at which the content being diffed is found. (This is useful when content contains location-relative information, like links.)
* ``a_headers``, ``b_headers``: Dict of HTTP headers.

Finally, some diffs take additional, diff-specific parameters.

**Return Values**

All diffs return a :class:`dict` with a key named ``"diff"``. The value of this dict entry varies by diff type, but is usually:

- An array of changes. Each entry will be a 2-tuple, where the first item is an :class:`int` reprenting the type of change (``-1`` for removal, ``0`` for unchanged, ``1`` for addition, or other numbers for diff-specific meanings) and the second item is the data or string that was added/removed/unchanged.

- A string representing a custom view of the diff, e.g. an HTML document.

- A bytestring representing a custom binary view of the diff, e.g. an image.

Each diff may add additional, diff-specifc keys to the dict. For example, :func:`web_monitoring_diff.html_diff_render` includes a ``"change_count"`` key indicating how many changes there were, since it’s tough
to inspect the HTML of the resulting diff and count yourself.


.. autofunction:: web_monitoring_diff.compare_length

.. autofunction:: web_monitoring_diff.identical_bytes

.. autofunction:: web_monitoring_diff.side_by_side_text

.. autofunction:: web_monitoring_diff.html_text_diff

.. autofunction:: web_monitoring_diff.html_source_diff

.. autofunction:: web_monitoring_diff.links_diff

.. autofunction:: web_monitoring_diff.links_diff_json

.. autofunction:: web_monitoring_diff.links_diff_html

.. autofunction:: web_monitoring_diff.html_diff_render

.. automodule:: web_monitoring_diff.experimental

    .. autofunction:: web_monitoring_diff.experimental.htmldiffer.diff

    .. autofunction:: web_monitoring_diff.experimental.htmltreediff.diff


Web Server
----------

.. autofunction:: web_monitoring_diff.server.make_app

.. autofunction:: web_monitoring_diff.server.cli


Exception Classes
-----------------

.. autoclass:: web_monitoring_diff.exceptions.UndecodableContentError

.. autoclass:: web_monitoring_diff.exceptions.UndiffableContentError
