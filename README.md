[![Download LatestVersion from PyPI](https://img.shields.io/pypi/v/web-monitoring-diff)](https://pypi.python.org/pypi/web-monitoring-diff) &nbsp;[![Code of Conduct](https://img.shields.io/badge/%E2%9D%A4-code%20of%20conduct-blue.svg?style=flat)](https://github.com/edgi-govdata-archiving/overview/blob/main/CONDUCT.md) &nbsp;[![Build Status](https://circleci.com/gh/edgi-govdata-archiving/web-monitoring-diff/tree/main.svg?style=shield)](https://circleci.com/gh/edgi-govdata-archiving/web-monitoring-diff) &nbsp;[![Documentation Status](https://readthedocs.org/projects/web-monitoring-diff/badge/?version=stable)](https://web-monitoring-diff.readthedocs.io/en/stable/?badge=stable)


# web-monitoring-diff

*Web-Monitoring-Diff* is a suite of functions that *diff* (find the differences between) types of content commonly found on the web, such as HTML, text files, etc. in a variety of ways. It also includes an optional web server that generates diffs as an HTTP service.

This package was originally built as a component of EDGI’s [Web Monitoring Project](https://github.com/edgi-govdata-archiving/web-monitoring), but is also used by other organizations and tools.


## Installation

*web-monitoring-diff* requires Python 3.7 or newer. Before anything else, make sure you’re using a supported version of Python. If you need to support different local versions of Python on your computer, we recommend using [pyenv](https://github.com/pyenv/pyenv) or [Conda](https://docs.conda.io/en/latest/).

1. *web-monitoring-diff* depends on several system-level libraries that you may need to install first. Specifically, you’ll need: `libxml2`, `libxslt`, `openssl`, and `libcurl`.

    **On MacOS,** we recommend installing these with [`Homebrew`](https://brew.sh/):

    ```sh
    brew install libxml2
    brew install libxslt
    brew install openssl
    # libcurl is built-in, so you generally don't need to install it
    ```

    **On Debian Linux:**

    ```sh
    apt-get install libxml2-dev libxslt-dev libssl-dev openssl libcurl4-openssl-dev
    ```

    **Other systems** may have different package managers or names for the packages, so you may need to look them up.

2. Install this package with *pip*. Be sure to include the `--no-binary lxml` option:

    ```sh
    pip install web-monitoring-diff --no-binary lxml
    ```

    Or, to also install the web server for generating diffs on demand, install the `server` extras:

    ```sh
    pip install web-monitoring-diff[server] --no-binary lxml
    ```

    The `--no-binary` flag ensures that pip downloads and builds a fresh copy of `lxml` (one of web-monitoring-diff’s dependencies) rather than using a pre-built version. It’s slower to install, but is required for all the dependencies to work correctly together. **If you publish a package that depends on web-monitoring-diff, your package will need to be installed with this flag, too.**

    **On MacOS,** you may need additional configuration to get `pycurl` use the Homebrew openssl. Try the following:

    ```sh
    PYCURL_SSL_LIBRARY=openssl \
      LDFLAGS="-L/usr/local/opt/openssl/lib" \
      CPPFLAGS="-I/usr/local/opt/openssl/include" \
      pip install web-monitoring-diff --no-binary lxml --no-cache-dir
    ```

    The `--no-cache-dir` flag tells `pip` to re-build the dependencies instead of using versions it’s built already. If you tried to install once before but had problems with `pycurl`, this will make sure pip actually builds it again instead of re-using the version it built last time around.

    **For local development,** make sure to do an editable installation instead. See [the “contributing” section](#contributing) below for more.

3. (Optional) Install experimental diffs. Some additional types of diffs are considered “experimental” — they may be new and still have lots of edge cases, may not be publicly available via PyPI or another package server, or may have any number of other issues. To install them, run:

    ```sh
    pip install -r requirements-experimental.txt
    ```


## Basic Usage

This package can imported as a library that provides diffing functions for use in your own python code, or it can be run as a standalone web server.


### Library Usage

Import `web_monitoring_diff`, then call a diff function:

```py
import web_monitoring_diff

page1 = "<!doctype html>\n<html><body>This is page 1.</body></html>"
page2 = "<!doctype html>\n<html><body>This is page 2.</body></html>"
comparison = web_monitoring_diff.html_diff_render(page1, page2)
```


### Web Server

Start the web server:

```sh
$ web-monitoring-diff-server
```

This starts the web server on port `8888`.

Then use cURL, a web browser, or any other HTTP tools to get a list of supported diff types:

```sh
$ curl "http://localhost:8888/"
```

That should output some JSON like:

```json
{"diff_types": ["length", "identical_bytes", "side_by_side_text", "links", "links_json", "html_text_dmp", "html_source_dmp", "html_token", "html_tree", "html_perma_cc", "links_diff", "html_text_diff", "html_source_diff", "html_visual_diff", "html_tree_diff", "html_differ"], "version": "0.1.0"}
```

You can use each of these diff types by requesting the URL:

```
http://localhost:8888/<diff_type>?a=<url_to_left_side_of_comparison>&b=<url_to_right_side_of_comparison>
```

For example, to compare how the links on the [National Renewable Energy Laboratory’s “About” page](https://www.nrel.gov/about/) changed between 2018 and 2020 using data from [the Internet Archive](https://web.archive.org/):

```sh
# URL of a version of the page archived in 2018:
$ VERSION_2018='http://web.archive.org/web/20180918073921id_/https://www.nrel.gov/about/'
# URL of a version of the page archived in 2020:
$ VERSION_2020='http://web.archive.org/web/20201006001420id_/https://www.nrel.gov/about/'
# Use the `links_json` diff to compare the page’s links and output as JSON:
$ curl "http://localhost:8888/links_json?a=${VERSION_2018}&b=${VERSION_2020}"
```

If you have `jq` installed, you might want to use it to format the result in a nicer way:

```sh
$ curl "http://localhost:8888/links_json?a=${VERSION_2018}&b=${VERSION_2020}" | jq
```

You can pass additional arguments to the various diffs in the querysting. See the full documentation of the server and off the various diffs for more details.


## Docker

You can deploy the web server as you might any Python application, or as a Docker image. We publish official images at: https://hub.docker.com/repository/docker/envirodgi/web-monitoring-diff. The most recent stable release is always available using the `:latest` tag.

Specific versions are tagged with the SHA-1 of the git commit they were built from. For example, the image `envirodgi/web-monitoring-diff:446ae83e121ec8c2207b2bca563364cafbdf8ce0` was built from [commit `446ae83e121ec8c2207b2bca563364cafbdf8ce0`](https://github.com/edgi-govdata-archiving/web-monitoring-diff/commit/446ae83e121ec8c2207b2bca563364cafbdf8ce0).

Note that, unlike running the command locally, the Docker image defaults to listening/serving on port 80 in the container. When you run it, you’ll want to map your ports. For example, to use port 8888 on your machine:

```sh
$ docker run -p 8888:80 envirodgi/web-monitoring-diff
```


### Building Images

To build a production image, use the `web-monitoring-diff` target:

```sh
# Build it:
$ docker build -t web-monitoring-diff .

# Then run it:
$ docker run -p 8888:80 web-monitoring-diff
```

Point your browser or ``curl`` at ``http://localhost:8888``.


## Code of Conduct

This repository falls under EDGI's [Code of Conduct](https://github.com/edgi-govdata-archiving/overview/blob/main/CONDUCT.md).


## Contributing

This project wouldn’t exist without a lot of amazing people’s help. It could use yours, too: please [file bugs or feature requests](https://github.com/edgi-govdata-archiving/web-monitoring-diff/issues) or make a [pull request](https://github.com/edgi-govdata-archiving/web-monitoring-diff/pulls) to address an issue or help improve the documentation.

If you’re looking for ways to help with the project, issues with the label [“good-first-issue”](https://github.com/edgi-govdata-archiving/web-monitoring-diff/issues?q=is%3Aissue+is%3Aopen+sort%3Aupdated-desc+label%3Agood-first-issue) are usually a good place to start.

When contributing to this project, please make sure to follow EDGI's [Code of Conduct](https://github.com/edgi-govdata-archiving/overview/blob/main/CONDUCT.md).


## Developing Locally

When developing locally, you’ll want to do an *editable install* from your local git checkout, rather than installing normally from PyPI as described in the [“installation” section](#installation) above.

First, make sure you have an appropriate Python version and the necessary system-level dependencies described above in the [“installation” section](#installation). Then:

1. Clone this repository wherever you’d like to edit it on your hard drive:

    ```sh
    $ git clone https://github.com/edgi-govdata-archiving/web-monitoring-diff.git
    $ cd web-monitoring-diff
    ```

2. Perform an *editable* install of the package in the repo:

    ```sh
    $ pip install -e . --no-binary lxml
    ```

3. Install additional experimental and development dependencies:

    ```sh
    $ pip install -r requirements-experimental.txt
    $ pip install -r requirements-dev.txt
    ```

4. Make sure it works without errors by running a python interpreter and importing the package:

    ```py
    import web_monitoring_diff
    ```

5. Edit some code!

6. Before pushing your commits and making a PR, run the tests and lint your code:

    ```sh
    # Run tests:
    $ pytest .

    # Lint your code to make sure it doesn't have any style issues:
    $ pyflakes .
    ```


## Contributors

Thanks to the following people for all their contributions! This project depends on their work.

<!-- ALL-CONTRIBUTORS-LIST:START -->
| Contributions | Name |
| ----: | :---- |
| [💻](# "Code") [⚠️](# "Tests") [🚇](# "Infrastructure") [📖](# "Documentation") [💬](# "Answering Questions") [👀](# "Reviewer") | [Dan Allan](https://github.com/danielballan) |
| [💻](# "Code") | [Vangelis Banos](https://github.com/vbanos) |
| [💻](# "Code") [📖](# "Documentation") | [Chaitanya Prakash Bapat](https://github.com/ChaiBapchya) |
| [💻](# "Code") [⚠️](# "Tests") [🚇](# "Infrastructure") [📖](# "Documentation") [💬](# "Answering Questions") [👀](# "Reviewer") | [Rob Brackett](https://github.com/Mr0grog) |
| [💻](# "Code") | [Stephen Buckley](https://github.com/StephenAlanBuckley) |
| [💻](# "Code") [📖](# "Documentation") [📋](# "Organizer") | [Ray Cha](https://github.com/weatherpattern) |
| [💻](# "Code") [⚠️](# "Tests") | [Janak Raj Chadha](https://github.com/janakrajchadha) |
| [💻](# "Code") | [Autumn Coleman](https://github.com/AutumnColeman) |
| [💻](# "Code") | [Luming Hao](https://github.com/lh00000000) |
| [🤔](# "Ideas and Planning") | [Mike Hucka](https://github.com/mhucka) |
| [💻](# "Code") | [Stuart Lynn](https://github.com/stuartlynn) |
| [💻](# "Code") [⚠️](# "Tests") | [Julian Mclain](https://github.com/julianmclain) |
| [💻](# "Code") | [Allan Pichardo](https://github.com/allanpichardo) |
| [📖](# "Documentation") [📋](# "Organizer") | [Matt Price](https://github.com/titaniumbones) |
| [💻](# "Code") | [Mike Rotondo](https://github.com/mrotondo) |
| [📖](# "Documentation") | [Susan Tan](https://github.com/ArcTanSusan) |
| [💻](# "Code") [⚠️](# "Tests") | [Fotis Tsalampounis](https://github.com/ftsalamp) |
| [📖](# "Documentation") [📋](# "Organizer") | [Dawn Walker](https://github.com/dcwalk) |
<!-- ALL-CONTRIBUTORS-LIST:END -->

(For a key to the contribution emoji or more info on this format, check out [“All Contributors.”](https://github.com/kentcdodds/all-contributors))


## License & Copyright

Copyright (C) 2017-2020 Environmental Data and Governance Initiative (EDGI)

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 3.0.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

See the [`LICENSE`](https://github.com/edgi-govdata-archiving/webpage-versions-processing/blob/main/LICENSE) file for details.
