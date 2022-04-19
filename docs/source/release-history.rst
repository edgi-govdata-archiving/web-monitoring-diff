===============
Release History
===============

Version 0.1.3 (2022-04-18)
--------------------------

This releases fixes some minor issues around content-type checking for HTML-related diffs (``html_diff_render`` and ``links_diff``). Both lean towards making content-type checking more lenient; our goal is to stop wasted diffing effort early *when we know it's not HTML,* not to only diff things are definitely HTML:

- Ignore invalid ``Content-Type`` headers. These happen fairly frequently in the wild — especially on HTML pages — and we now ignore them instead of treating them as implying the content is not HTML. (:issue:`76`)

- Ignore the ``application/x-download`` content type. This content-type isn't really about the content, but is frequently used to make a browser download a file rather than display it inline. It no longer affects parsing or diffing. (:issue:`105`)

This release also adds some nice sidebar links for documentation, the changelog, issues, and source code to PyPI. (:issue:`107`)


Version 0.1.2 (2021-04-01)
--------------------------

- The server uses a pool of child processes to run diffs. If the pool breaks while running a diff, it will be re-created once, and, if it fails again, the server will now crash with an exit code of ``10``. (An external process manager like Supervisor, Kubernetes, etc. can then decide how to handle the situation.) Previously, the diff would fail at this point, but server would try to re-create the process pool again the next time a diff was requested. You can opt-in to the old behavior by setting the ``RESTART_BROKEN_DIFFER`` environment variable to ``true``. (:issue:`49`)

- The diff server now requires Sentry 1.x for error tracking.


Version 0.1.2rc1 (2021-01-01)
-----------------------------

- The server uses a pool of child processes to run diffs. If the pool breaks while running a diff, it will be re-created once, and, if it fails again, the server will now crash with an exit code of ``10``. (An external process manager like Supervisor, Kubernetes, etc. can then decide how to handle the situation.) Previously, the diff would fail at this point, but server would try to re-create the process pool again the next time a diff was requested. You can opt-in to the old behavior by setting the ``RESTART_BROKEN_DIFFER`` environment variable to ``true``. (:issue:`49`)


Version 0.1.1 (2020-11-24)
--------------------------

This is a bugfix release that focuses on :func:`web_monitoring_diff.html_diff_render` and the server.

- Fix an issue where the diffing server could reset the process pool that manages the actual diffs multiple times unnecessarily, leading to wasted memory and CPU. If you are tracking logs and errors, this will also make error messages about the diffing server clearer — you’ll see “BrokenProcessPool” instead of “'NoneType' object does not support item assignment.” (:issue:`38`)

- Ensure the server shuts down gracefully when pressing ctrl+c or sending a SIGINT signal. (:issue:`44`)

- Fix :func:`web_monitoring_diff.html_diff_render` to make sure the spacing of text and tags in the HTML source code of the diff matches the original. This resolves display issues on pages where CSS is used to treat spacing as significant. (:issue:`40`)

- Improve handling of lazy-loaded images in :func:`web_monitoring_diff.html_diff_render`. When images are lazy-loaded via JS, they usually use the ``data-src`` or ``data-srcset`` attributes, and we now check those, too. Additionally, if two images have no detectable URLs, we now treat them as the same, rather than different. (:issue:`39`)

- Stop showing inline scripts and styles in :func:`web_monitoring_diff.html_diff_render`. These still get wrapped with ``<del>`` or ``<ins>`` elements, but they don’t show up visually since they aren’t elements that should be visually rendered. (:issue:`43`)


Version 0.1.0
-------------

This project used to be a part of `web-monitoring-processing <https://github.com/edgi-govdata-archiving/web-monitoring-processing/>`_, which contains a wide variety of libraries, scripts, and other tools for working with data across all the various parts of EDGI’s Web Monitoring project. The goal of this initial release is to create a new, more focused package containing the diff-releated tools so they can be more easily used by others.

This release is more-or-less the same code that was a part of ``web-monitoring-processing``, although the public API has been rearranged very slightly to make sense in this new, stand-alone context.
