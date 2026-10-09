FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 10001 spike \
 && mkdir /data && chown spike:spike /data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[iceberg,pychess]"

# ---- tests: docker build --target test . ----
FROM base AS test
RUN pip install ".[dev]"
COPY tests ./tests
USER spike
RUN pytest -q -p no:cacheprovider

# ---- runtime ----
FROM base AS runtime
USER spike
# A named volume mounted here inherits the spike user's ownership.
VOLUME ["/data"]
WORKDIR /data
ENTRYPOINT ["pgn-timing"]
CMD ["--help"]
