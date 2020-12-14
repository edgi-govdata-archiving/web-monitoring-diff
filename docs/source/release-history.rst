===============
Release History
===============

In Development
--------------

- The server uses a pool of child processes to run diffs. If the pool breaks while running a diff, it will be re-created once, and, if it fails again, the server will now crash with an exit code of ``10``. (An external process manager like Supervisor, Kubernetes, etc. can then decide how to handle the situation.) Previously, the diff would fail at this point, but server would try to re-create the process pool again the next time a diff was requested. You can opt-in to the old behavior by setting the ``RESTART_BROKEN_DIFFER`` environment variable to ``true``. (`#49 <https://github.com/edgi-govdata-archiving/web-monitoring-diff/issues/49>`_)


Version 0.1.1 (2020-11-24)
--------------------------

This is a bugfix release that focuses on :func:`web_monitoring_diff.html_diff_render` and the server.

- Fix an issue where the diffing server could reset the process pool that manages the actual diffs multiple times unnecessarily, leading to wasted memory and CPU. If you are tracking logs and errors, this will also make error messages about the diffing server clearer — you’ll see “BrokenProcessPool” instead of “'NoneType' object does not support item assignment.” (`#38 <https://github.com/edgi-govdata-archiving/web-monitoring-diff/issues/38>`_)

- Ensure the server shuts down gracefully when pressing ctrl+c or sending a SIGINT signal. (`#44 <https://github.com/edgi-govdata-archiving/web-monitoring-diff/issues/44>`_)

- Fix :func:`web_monitoring_diff.html_diff_render` to make sure the spacing of text and tags in the HTML source code of the diff matches the original. This resolves display issues on pages where CSS is used to treat spacing as significant. (`#40 <https://github.com/edgi-govdata-archiving/web-monitoring-diff/issues/40>`_)

- Improve handling of lazy-loaded images in :func:`web_monitoring_diff.html_diff_render`. When images are lazy-loaded via JS, they usually use the ``data-src`` or ``data-srcset`` attributes, and we now check those, too. Additionally, if two images have no detectable URLs, we now treat them as the same, rather than different. (`#39 <https://github.com/edgi-govdata-archiving/web-monitoring-diff/issues/39>`_)

- Stop showing inline scripts and styles in :func:`web_monitoring_diff.html_diff_render`. These still get wrapped with ``<del>`` or ``<ins>`` elements, but they don’t show up visually since they aren’t elements that should be visually rendered. (`#43 <https://github.com/edgi-govdata-archiving/web-monitoring-diff/issues/43>`_)


Version 0.1.0
-------------

This project used to be a part of `web-monitoring-processing <https://github.com/edgi-govdata-archiving/web-monitoring-processing/>`_, which contains a wide variety of libraries, scripts, and other tools for working with data across all the various parts of EDGI’s Web Monitoring project. The goal of this initial release is to create a new, more focused package containing the diff-releated tools so they can be more easily used by others.

This release is more-or-less the same code that was a part of ``web-monitoring-processing``, although the public API has been rearranged very slightly to make sense in this new, stand-alone context.
