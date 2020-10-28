============
Installation
============

*web-monitoring-diff* requires **Python 3.7 or newer**. Before anything else, make sure you’re using a supported version of Python. If you need to support different local versions of Python on your computer, we recommend using `pyenv`_ or `Conda`_.

1. **System-level dependencies:** web-monitoring-diff depends on several system-level, non-Python libraries that you may need to install first. Specifically, you’ll need: ``libxml2``, ``libxslt``, ``openssl``, and ``libcurl``.

  **On MacOS,** we recommend installing these with `Homebrew`_:

  .. code-block:: bash

    brew install libxml2
    brew install libxslt
    brew install openssl
    # libcurl is built-in, so you generally don't need to install it

  **On Debian Linux,** use ``apt``:

  .. code-block:: bash

    apt-get install libxml2-dev libxslt-dev libssl-dev openssl libcurl4-openssl-dev

  **Other systems** may have different package managers or names for the packages, so you may need to look them up.

2. **Install this package** with *pip*. Be sure to include the ``--no-binary lxml`` option:

  .. code-block:: bash

    pip install web-monitoring-diff --no-binary lxml

  Or, to also install the web server for generating diffs on demand, install the ``server`` extras:

  .. code-block:: bash

      pip install web-monitoring-diff[server] --no-binary lxml

  The ``--no-binary`` flag ensures that pip downloads and builds a fresh copy of ``lxml`` (one of web-monitoring-diff’s dependencies) rather than using a pre-built version. It’s slower to install, but is required for all the dependencies to work correctly together. **If you publish a package that depends on web-monitoring-diff, your package will need to be installed with this flag, too.**

  **On MacOS,** you may need additional configuration to get ``pycurl`` to use the Homebrew `openssl`. Try the following:

  .. code-block:: bash

    PYCURL_SSL_LIBRARY=openssl \
      LDFLAGS="-L/usr/local/opt/openssl/lib" \
      CPPFLAGS="-I/usr/local/opt/openssl/include" \
      pip install web-monitoring-diff --no-binary lxml --no-cache-dir

  The ``--no-cache-dir`` flag tells *pip* to re-build the dependencies instead of using versions it’s built already. If you tried to install once before but had problems with ``pycurl``, this will make sure pip actually builds it again instead of re-using the version it built last time around.

  **For local development,** clone the git repository and then make sure to do an editable installation instead.

  .. code-block:: bash

      pip install .[server,dev] --no-binary lxml

3. **(Optional) Install experimental diffs.** Some additional types of diffs are considered “experimental” — they may be new and still have lots of edge cases, may not be publicly available via PyPI or another package server, or may have any number of other issues. To install them, run:

  .. code-block:: bash

    pip install -r requirements-experimental.txt


.. _pyenv: https://github.com/pyenv/pyenv
.. _conda: https://docs.conda.io/en/latest/
.. _Homebrew: https://brew.sh/
