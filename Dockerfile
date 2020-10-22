# Use an official Python runtime as a parent image
FROM python:3.7-slim
LABEL maintainer="enviroDGI@gmail.com"

RUN apt-get update && apt-get install -y --no-install-recommends \
    git gcc g++ pkg-config libxml2-dev libxslt-dev libz-dev \
    libssl-dev openssl libcurl4-openssl-dev

# Set the working directory to /app
WORKDIR /app

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

# Install package.
RUN pip install .

# Make port 80 available to the world outside this container.
EXPOSE 80

# Run server on port 80 when the container launches.
CMD ["web-monitoring-diff-server", "80"]
