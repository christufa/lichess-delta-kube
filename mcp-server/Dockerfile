# chess-mcp: built by .github/workflows/images.yml as lichess-mcp (tests run in the `test` stage).
FROM ghcr.io/astral-sh/uv:0.13.4 AS uv

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=uv /uv /usr/local/bin/uv
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin mcp
WORKDIR /app
# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# ---- tests: docker build --target test . ----
FROM base AS test
RUN uv sync --locked --no-editable
COPY tests ./tests
USER mcp
RUN /opt/venv/bin/pytest -q

# ---- runtime: no uv, no sources, just the virtualenv ----
FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH
# No pip at runtime: nothing is installed after the build, and pip carries its own vendored
# packages (e.g. certifi) that scanners flag. The venv was built in the base stage.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin mcp \
 && python -m pip uninstall --yes --quiet pip
COPY --from=base /opt/venv /opt/venv
USER 10001
EXPOSE 8000
ENTRYPOINT ["chess-mcp"]
