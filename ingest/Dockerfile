FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LICHESS_WORK=/work

RUN useradd --create-home --uid 10001 ingest \
 && mkdir /work && chown ingest:ingest /work

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# ---- tests: docker build --target test . ----
FROM base AS test
RUN pip install ".[dev]"
COPY tests ./tests
USER ingest
RUN pytest -q -p no:cacheprovider

# ---- runtime ----
FROM base AS runtime
USER ingest
VOLUME ["/work"]
WORKDIR /work
ENTRYPOINT ["lichess-ingest"]
CMD ["--help"]
