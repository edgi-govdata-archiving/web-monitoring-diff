===========
Basic Usage
===========

*web-monitoring-diff* can imported as a library that provides diffing functions for use in your own Python code, or it can be run as a standalone web server.

- :ref:`usage_library`
- :ref:`usage_server`


.. _usage_library:

Library Usage
=============

Import `web_monitoring_diff`, then call a diff function:

.. code-block:: python

  import web_monitoring_diff

  page1 = "<!doctype html>\n<html><body>This is page 1.</body></html>"
  page2 = "<!doctype html>\n<html><body>This is page 2.</body></html>"
  comparison = web_monitoring_diff.html_diff_render(page1, page2)


.. _usage_server:

Web Server
==========

Start the web server:

.. code-block:: bash

  $ web-monitoring-diff-server

This starts the web server on port `8888`.

Then use cURL, a web browser, or any other HTTP tools to get a list of supported diff types:

.. code-block:: bash

  $ curl "http://localhost:8888/"

That should output some JSON like:

.. code-block:: json

  {"diff_types": ["length", "identical_bytes", "side_by_side_text", "links", "links_json", "html_text_dmp", "html_source_dmp", "html_token", "html_tree", "html_perma_cc", "links_diff", "html_text_diff", "html_source_diff", "html_visual_diff", "html_tree_diff", "html_differ"], "version": "0.1.0"}

You can use each of these diff types by requesting the URL:

.. code-block::

  http://localhost:8888/<diff_type>?a=<url_to_left_side_of_comparison>&b=<url_to_right_side_of_comparison>

For example, to compare how the links on the `National Renewable Energy Laboratory’s “About” page <https://www.nrel.gov/about/>`_ changed between 2018 and 2020 using data from the `Internet Archive`_:

.. code-block:: bash

  # URL of a version of the page archived in 2018:
  $ VERSION_2018='http://web.archive.org/web/20180918073921id_/https://www.nrel.gov/about/'
  # URL of a version of the page archived in 2020:
  $ VERSION_2020='http://web.archive.org/web/20201006001420id_/https://www.nrel.gov/about/'
  # Use the `links_json` diff to compare the page’s links and output as JSON:
  $ curl "http://localhost:8888/links_json?a=${VERSION_2018}&b=${VERSION_2020}"

If you have ``jq`` installed, you might want to use it to format the result in a nicer way:

.. code-block:: bash

  $ curl "http://localhost:8888/links_json?a=${VERSION_2018}&b=${VERSION_2020}" | jq

You can pass additional arguments to the various diffs in the querysting. See the full documentation of the server and off the various diffs for more details.


.. _internet archive: https://web.archive.org/
