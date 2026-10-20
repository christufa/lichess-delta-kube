FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LICHESS_WORK=/work

# aria2c: BitTorrent downloads of the monthly files (falls back to HTTPS without it)
RUN apt-get update \
 && apt-get install -y --no-install-recommends aria2 ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 ingest \
 && mkdir /work && chown ingest:ingest /work

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# ---- tests: docker build --target test . ----
FROM base AS test
RUN pip install ".[dev]"
COPY tests ./tests
USER 10001
RUN pytest -q -p no:cacheprovider

# ---- runtime ----
# Numeric USER (the uid of "ingest" above): with runAsNonRoot the kubelet must be able to
# verify the user isn't root, which it can't for a name.
FROM base AS runtime
USER 10001
VOLUME ["/work"]
WORKDIR /work
ENTRYPOINT ["lichess-ingest"]
CMD ["--help"]
