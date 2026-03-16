##
# `base` contains only the core system-level dependencies needed to run
# the diff server. `dev` builds on it by adding compile-time support for the
# same packages so that we can build the Python dependencies that have C code,
# like `lxml`.
# We separate them out so that the final `release` image can layer on top of
# this one without needing compiler-related packages.
##
FROM python:3.10.20-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    libxslt1.1 \
    libz1 \
    openssl \
    libcurl4 \
 && rm -rf /var/lib/apt/lists/*


##
# `dev` is an intermediate image used for building compiled dependencies
# or as a development container.
##

FROM base AS dev

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    gcc \
    g++ \
    pkg-config \
    # Compiler-support for the system dependencies in `base`
    libxml2-dev \
    libxslt-dev \
    libz-dev \
    libssl-dev \
    libcurl4-openssl-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --upgrade pip
RUN pip install cchardet

ADD pyproject.toml README.md /app/
# Create the folder where the version file needs to be written
RUN mkdir -p /app/web_monitoring_diff
# Set an environment variable to bypass the Git version check during dependency install.
# Note: This dummy version (0.0.0) is temporary. The second install step later in
# the Dockerfile (lines 43-45) will overwrite and correct the version number.
RUN SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0 pip install ".[server]" --no-binary lxml
# Some experimental dependencies are not allowed as package extras, so we
# install via separate means.
RUN pip install --trusted-host pypi.python.org -r requirements-experimental.txt

# Copy the rest of the source.
ADD . /app
# ...and install!
RUN pip install ".[server]" --no-binary lxml


##
# `release` is the final production image.
##

FROM base AS release


LABEL org.opencontainers.image.title="web-monitoring-diff" \
      org.opencontainers.image.description="Server for detecting changes in web content" \
      org.opencontainers.image.authors="Environmental Data & Governance Initiative (EDGI)" \
      org.opencontainers.image.url="https://envirodatagov.org/" \
      org.opencontainers.image.source="https://github.com/edgi-govdata-archiving/web-monitoring-diff" \
      org.opencontainers.image.documentation="https://web-monitoring-diff.readthedocs.io/" \
      org.opencontainers.image.licenses="GPL-3.0-only"

COPY --from=dev /usr/local/lib/ /usr/local/lib/
COPY --from=dev /usr/local/bin/ /usr/local/bin/

ENV LD_LIBRARY_PATH=/usr/local/lib

EXPOSE 80

CMD ["web-monitoring-diff-server", "--port", "80"]
