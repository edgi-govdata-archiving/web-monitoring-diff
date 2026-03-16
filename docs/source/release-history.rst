===============
Release History
===============

Version 0.2.0 (2026-03-16)
--------------------------

Current versions of Python (3.10.x through 3.15.0a7) are finally supported in this release of web-monitoring-diff! Please note this release also requires Python 3.10.0 at a minimum.

There are number of other notable changes and new features:


Breaking Changes
^^^^^^^^^^^^^^^^

- The minimum required version of Python is now v3.10.0.

- Sentry-sdk v2.x is required for the *server* module. Make sure this is compatible with other packages you are using, and if you are self-hosting your Sentry server, make sure it is compatible as well.

- cChardet is now optional. In prior releases, the *server* module required cChardet, an extremely fast and accurate package for detecting text encoding. However, cChardet is not actively maintained and, does not support Python 3.11 and newer, and could be hard to install on some systems. By default, web-monitoring-diff now uses a slower, better-supported version of chardet, but will automatically use cChardet if you install it alongside web-monitoring-diff.


Features
^^^^^^^^

- Use the new ``MAX_DIFFS_PER_WORKER`` environment variable to restart worker processes that perform the actual diffs in the diff server.

  When set to a positive integer, the server will restart a worker processes after it has performed this many diffs (the number of workers can be controlled with ``DIFFER_PARALLELISM``, which is not new). If ``0`` or not set, workers will only be restarted if they crash. Setting this appropriately can help keep resources within limits and prevent eventual hangs or crashes. (:issue:`210`)


Fixes
^^^^^

- Fix XML prolog detection in diff server. This could occasionally have inferred character encoding in an XML document that was inaccurate. (:issue:`209`)


Docs & Internals
^^^^^^^^^^^^^^^^

- The documentation now has a :doc:`“configuration” page <configuration>` that describes all the environment variables you can use to configure the various diff algorithms and the server. (:issue:`231`)

- Package metadata is now managed using the modern `pyproject.toml` format. (:issue:`224`)

- The Docker image (https://hub.docker.com/r/envirodgi/web-monitoring-diff) now includes standardized labels from OCI (Open Containers Initiative). (:issue:`227`)


New Contributors
^^^^^^^^^^^^^^^^

- `aaxis-em <https://github.com/aaxis-em>`_

- `Beckett Frey <https://github.com/BeckettFrey>`_

- `Derzan Chiang <https://github.com/MiTo0o>`_


Version 0.1.7 (2025-10-06)
--------------------------

Support lxml v6. (:issue:`207`)


Version 0.1.6 (2025-01-24)
--------------------------

Remove stray logging statements that should not have been included in v0.1.5. (:issue:`194`)


Version 0.1.5 (2025-01-23)
--------------------------

Treat `binary/octet-stream` as a generic media type, just like `application/octet-stream`, when trying to determine if content is not HTML. Even though `binary/octet-stream` is not a registered IANA media type, it turns out some AWS SDKs use it when uploading files to S3, so it’s not uncommon. (:issue:`190`)


Version 0.1.4 (2024-01-01)
--------------------------

This is a minor release that updates some of the lower-level parsing and diffing tools this package relies on:

- Updates the diff-match-patch implementation we rely on for simple text diffs to `fast_diff_match_patch v2.x <https://pypi.org/project/fast-diff-match-patch/>`_. (:issue:`126`)

- Fix misconfigured dependency requirements for html5-parser. This should have no user impact, since there are no releases (yet) in the version range we were accidentally allowing for. (:issue:`126`)

- Support lxml v5.x. (:issue:`163`)


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
