"""The FastAPI app: public dashboard endpoints under /api/v1, probes at /healthz and /readyz,
and one internal endpoint for Grafana under /internal/v1.

cloudflared forwards https://chess.generops.dev/api/* with the path unchanged, so routes
carry the /api prefix; the probes and /internal stay outside it and are never routed publicly.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from collections.abc import Callable
from importlib.metadata import version
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from . import queries
from .caching import RateLimiter, ResponseCache
from .models import (
    MONTH_PATTERN,
    Costs,
    Health,
    Months,
    Openings,
    Problem,
    RatingDistribution,
    Speed,
    Summary,
    SummarySpeed,
)
from .settings import Settings
from .trino_client import QueryRunner, TrinoRunner, TrinoUnavailable

log = logging.getLogger("chess_api")

MonthParam = Annotated[str, Query(pattern=MONTH_PATTERN, examples=["2015-12"], description="YYYY-MM")]
SummarySpeeds = Annotated[
    list[SummarySpeed], Query(description="Repeat for several; empty = every speed and `all`")
]
Speeds = Annotated[list[Speed], Query(description="Repeat for several; empty = every speed")]

ERRORS = {
    400: {"model": Problem, "description": "Invalid range"},
    429: {"model": Problem, "description": "Too many requests from this IP; see Retry-After"},
    503: {"model": Problem, "description": "The data store is unavailable; retry later"},
}
CACHED = {304: {"description": "Not modified (If-None-Match matched the ETag)"}}


def _check_range(from_month: str, to_month: str) -> None:
    if from_month > to_month:
        raise HTTPException(400, "`from` must not be after `to`")


def create_app(settings: Settings | None = None, db: QueryRunner | None = None) -> FastAPI:
    settings = settings or Settings()
    db = db or TrinoRunner(settings)
    cache = ResponseCache(settings.cache_ttl_seconds, settings.cache_max_entries)
    limiter = RateLimiter(settings.rate_limit_per_second, settings.rate_limit_burst)

    app = FastAPI(
        title="Lichess lakehouse API",
        version=version("chess-api"),
        description=(
            "Read-only aggregates of the Lichess game history (lake.gold). Data changes once a "
            "month; responses are cacheable (ETag, Cache-Control) and rate-limited per IP."
        ),
        generate_unique_id_function=lambda route: route.name,  # operationId = function name
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.cache = cache
    app.state.db = db

    def respond(request: Request, key: str, compute: Callable[[], BaseModel]) -> Response:
        entry, hit = cache.get_or_compute(key, lambda: compute().model_dump_json().encode())
        request.state.cache = "hit" if hit else "miss"
        headers = {"Cache-Control": settings.cache_control, "ETag": entry.etag, "X-Cache": request.state.cache}
        if entry.etag in [t.strip() for t in request.headers.get("if-none-match", "").split(",")]:
            return Response(status_code=304, headers=headers)
        return Response(entry.body, media_type="application/json", headers=headers)

    # ---------------------------------------------------------------- dashboard endpoints

    @app.get("/api/v1/months", response_model=Months, responses={**CACHED, **ERRORS}, tags=["dashboard"])
    def get_months(request: Request) -> Response:
        """Months that have data, oldest first."""
        return respond(request, "months", lambda: queries.months(db))

    @app.get("/api/v1/summary", response_model=Summary, responses={**CACHED, **ERRORS}, tags=["dashboard"])
    def get_summary(
        request: Request,
        from_month: Annotated[str, Query(alias="from", pattern=MONTH_PATTERN, examples=["2013-01"])],
        to_month: Annotated[str, Query(alias="to", pattern=MONTH_PATTERN, examples=["2015-12"])],
        speed: SummarySpeeds = [],  # noqa: B006 (FastAPI copies defaults)
    ) -> Response:
        """Games, players and results per month and speed: the dashboard's time series."""
        _check_range(from_month, to_month)
        speeds = sorted(set(speed))
        key = f"summary:{from_month}:{to_month}:{','.join(speeds)}"
        return respond(request, key, lambda: queries.summary(db, from_month, to_month, speeds))

    @app.get(
        "/api/v1/rating-distribution",
        response_model=RatingDistribution,
        responses={**CACHED, **ERRORS},
        tags=["dashboard"],
    )
    def get_rating_distribution(
        request: Request,
        month: MonthParam,
        speed: Speeds = [],  # noqa: B006 (FastAPI copies defaults)
    ) -> Response:
        """Players' ratings in 50-point buckets for one month, per speed."""
        speeds = sorted(set(speed))
        key = f"ratings:{month}:{','.join(speeds)}"
        return respond(request, key, lambda: queries.rating_distribution(db, month, speeds))

    @app.get("/api/v1/openings", response_model=Openings, responses={**CACHED, **ERRORS}, tags=["dashboard"])
    def get_openings(
        request: Request,
        from_month: Annotated[str, Query(alias="from", pattern=MONTH_PATTERN, examples=["2015-01"])],
        to_month: Annotated[str, Query(alias="to", pattern=MONTH_PATTERN, examples=["2015-12"])],
        speed: Annotated[Speed | None, Query(description="Omit for every speed")] = None,
        rating_band: Annotated[
            int | None,
            Query(ge=0, le=4000, multiple_of=200, description="Lower bound of a 200-point band; omit for all"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> Response:
        """Most played openings over a month range, by speed and rating band."""
        _check_range(from_month, to_month)
        key = f"openings:{from_month}:{to_month}:{speed}:{rating_band}:{limit}"
        return respond(
            request, key, lambda: queries.openings(db, from_month, to_month, speed, rating_band, limit)
        )

    # ---------------------------------------------------------------- internal (Grafana)

    @app.get("/internal/v1/costs", response_model=Costs, include_in_schema=False)
    def get_costs(request: Request, months: Annotated[int, Query(ge=1, le=24)] = 3) -> Response:
        """Daily Azure cost by service for the last `months` months (this one included), for the
        Grafana dashboard. Not public: cloudflared only routes /api/, the network policy only lets
        Grafana in, and a request that came through Cloudflare anyway is answered 404."""
        if settings.client_ip_header in request.headers:
            raise HTTPException(404, "Not Found")
        today = dt.datetime.now(dt.UTC).date()
        y, m = divmod(today.year * 12 + today.month - 1 - (months - 1), 12)
        from_month = f"{y:04d}-{m + 1:02d}"
        return respond(request, f"costs:{from_month}", lambda: queries.costs(db, from_month))

    # ---------------------------------------------------------------- probes

    ready_lock = threading.Lock()
    last_ready = [0.0]  # monotonic time of the last successful Trino check

    @app.get("/healthz", response_model=Health, tags=["probes"])
    def healthz() -> Health:
        """Liveness: the process answers."""
        return Health(status="ok")

    @app.get("/readyz", response_model=Health, responses={503: {"model": Problem}}, tags=["probes"])
    def readyz() -> Health:
        """Readiness: Trino answers a query as chess-api (checked at most every 30 s)."""
        with ready_lock:
            if time.monotonic() - last_ready[0] > settings.ready_check_seconds:
                db.query("SELECT 1")  # TrinoUnavailable → 503
                last_ready[0] = time.monotonic()
        return Health(status="ok")

    # ---------------------------------------------------------------- errors, middleware

    @app.exception_handler(TrinoUnavailable)
    def trino_unavailable(request: Request, exc: TrinoUnavailable) -> JSONResponse:
        return JSONResponse(
            {"detail": "data store unavailable"}, status_code=503, headers={"Retry-After": "30"}
        )

    @app.middleware("http")
    async def rate_limit_and_log(request: Request, call_next):
        started = time.perf_counter()
        ip = client_ip(request, settings.client_ip_header)
        public = request.url.path.startswith("/api/")
        wait = limiter.take(ip) if public and limiter.enabled else 0.0
        if wait:
            response: Response = JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(max(1, round(wait)))},
            )
        else:
            response = await call_next(request)
        if public or response.status_code >= 400:
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "query": request.url.query,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "client_ip": ip,
                    "cache": getattr(request.state, "cache", None),
                },
            )
        return response

    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")
    return app


def client_ip(request: Request, header: str) -> str:
    forwarded = request.headers.get(header, "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"
