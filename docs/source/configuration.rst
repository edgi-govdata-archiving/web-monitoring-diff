=============
Configuration
=============

The diff server and some diffing algorithms can be configured via environment variables. All variables are optional; defaults are noted below.

A template listing all variables is available in `.env.example <https://github.com/edgi-govdata-archiving/web-monitoring-diff/blob/main/.env.example>`_ at the root of the repository.


Diff Settings
=============

These variables affect the behavior of diff algorithms.

.. envvar:: DIFFER_COLOR_INSERTION

   CSS color value used to highlight *insertions* in :func:`~web_monitoring_diff.html_diff_render`, :func:`~web_monitoring_diff.links_diff`, and :func:`~web_monitoring_diff.links_diff_html`.

   Default: ``#4dac26``

.. envvar:: DIFFER_COLOR_DELETION

   CSS color value used to highlight *deletions* in :func:`~web_monitoring_diff.html_diff_render`, :func:`~web_monitoring_diff.links_diff`, and :func:`~web_monitoring_diff.links_diff_html`.

   Default: ``#d01c8b``


Server Settings
===============

These variables affect the behavior of the diff web server.

.. envvar:: DIFFING_SERVER_DEBUG

   Set to ``true`` to enable debug mode. In debug mode, the server returns full tracebacks in error responses and automatically reloads when source files change.

   Default: ``False``

.. envvar:: ACCESS_CONTROL_ALLOW_ORIGIN_HEADER

   Value to use for the ``Access-Control-Allow-Origin`` HTTP response header, enabling CORS requests. Set to an empty string to disable the header entirely.

   Default: ``*`` (allow all origins)

.. envvar:: DIFFER_MAX_BODY_SIZE

   Maximum size (in bytes) of a response body that the server will attempt to diff. Requests for content larger than this will be rejected.

   Default: ``10485760`` (10 MB)

.. envvar:: USE_SIMPLE_HTTP_CLIENT

   Set to ``true`` to use Tornado's built-in simple HTTP client when fetching pages to diff. By default, the server uses a cURL-based client, which is generally faster and more robust.

   Default: unset (uses cURL-based client)

.. envvar:: VALIDATE_TARGET_CERTIFICATES

   Set to ``true`` to require valid SSL certificates when fetching ``https://`` pages to diff. By default, the server does *not* validate SSL certificates.

   Default: unset (certificates are not validated)

.. envvar:: DIFFER_PARALLELISM

   Number of diffs that can run in parallel (size of the worker process pool).

   Default: unset (Tornado chooses a default based on CPU count)

.. envvar:: MAX_DIFFS_PER_WORKER

   After a worker process has completed this many diffs, it is terminated and replaced with a fresh process. This helps reclaim memory that accumulates over time. Set to ``0`` or leave unset to disable recycling.

   Default: unset (workers run indefinitely)

.. envvar:: RESTART_BROKEN_DIFFER

   Set to ``true`` to keep accepting diff requests even if the worker process pool crashes, and attempt to restart the pool automatically. By default, a pool crash causes the server to stop accepting requests.

   Default: unset (server does not restart a broken pool)
