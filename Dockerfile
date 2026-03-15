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
    libxml2-dev \
    libxslt-dev \
    libz-dev \
    libssl-dev \
    libcurl4-openssl-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --upgrade pip
RUN pip install cchardet

ADD requirements.txt /app
RUN pip install --trusted-host pypi.python.org -r requirements.txt

ADD requirements-server.txt /app
RUN pip install --trusted-host pypi.python.org -r requirements-server.txt

ADD requirements-experimental.txt /app
RUN pip install --trusted-host pypi.python.org -r requirements-experimental.txt

ADD . /app

RUN pip install .[server] --no-binary lxml


##
# `release` is the final production image.
##

FROM base AS release

ARG VERSION=unknown
ARG REVISION=unknown
ARG CREATED=unknown

LABEL org.opencontainers.image.title="web-monitoring-diff" \
      org.opencontainers.image.description="Tools and server for detecting changes in web content" \
      org.opencontainers.image.authors="enviroDGI@gmail.com" \
      org.opencontainers.image.source="https://github.com/edgi-govdata-archiving/web-monitoring-diff" \
      org.opencontainers.image.documentation="https://github.com/edgi-govdata-archiving/web-monitoring-diff#readme" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version=$VERSION \
      org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.created=$CREATED \
      org.opencontainers.image.base.name="python:3.10.17-slim"

COPY --from=dev /usr/local/lib/ /usr/local/lib/
COPY --from=dev /usr/local/bin/ /usr/local/bin/

ENV LD_LIBRARY_PATH=/usr/local/lib
WORKDIR /app

EXPOSE 80

CMD ["web-monitoring-diff-server", "--port", "80"]
