##
# `base` contains the only the core system-level dependencies needed to run
# the diff server. `dev` builds on it by adding compile-time support for the
# same packages so that we can build the Python dependencies that have C code,
# like `lxml`.
# We separate them out so that the final `release` image can layer on top of
# this one without needing compiler-related packages.
##
FROM python:3.10.17-slim as base
LABEL maintainer="enviroDGI@gmail.com"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 libxslt1.1 libz1 openssl libcurl4


##
# `dev` is an intermediate image that is used for building compiled
# dependencies or can be used as a development environment if you want to work
# in a Docker container.
##
FROM base as dev

RUN apt-get update && apt-get install -y --no-install-recommends \
    git gcc g++ pkg-config \
    # Compiler-support for the system dependencies in `base`
    libxml2-dev libxslt-dev libz-dev libssl-dev libcurl4-openssl-dev

# Set the working directory to /app
WORKDIR /app

RUN pip install --upgrade pip
RUN pip install cchardet
# Copy the requirements.txt alone into the container at /app
# so that they can be cached more aggressively than the rest of the source.
ADD requirements.txt /app
RUN pip install --trusted-host pypi.python.org -r requirements.txt
ADD requirements-server.txt /app
RUN pip install --trusted-host pypi.python.org -r requirements-server.txt
ADD requirements-experimental.txt /app
RUN pip install --trusted-host pypi.python.org -r requirements-experimental.txt

# Copy the rest of the source.
ADD . /app
# ...and install!
RUN pip install .[server] --no-binary lxml


##
# `release` is the final, release-ready image with only the necessary
# dependencies to run all diffs and the diff server.
##
FROM base as release

# Copy built python dependencies.
COPY --from=dev /usr/local/lib/ /usr/local/lib/
# Copy executables.
COPY --from=dev /usr/local/bin /usr/local/bin

ENV LD_LIBRARY_PATH=/usr/local/lib

EXPOSE 80
CMD ["web-monitoring-diff-server", "--port", "80"]
