"""HTTP: the chat endpoint the dashboard's panel talks to (Vercel AI SDK UI message stream).

  POST /api/v1/chat          stream an answer; Entra token with Chat.User required
  GET  /api/v1/chat/status   for a signed-in user: may they chat, what's left today
  GET  /healthz              liveness (not routed publicly)

cloudflared routes https://chess.<domain>/api/v1/chat* here (before the data API's /api/ rule).
"""
# No `from __future__ import annotations` here: FastAPI resolves the endpoints' Annotated[...,
# Depends(signed_in)] at runtime, and signed_in is local to create_app.
import logging
import threading
import time
from collections import deque
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic_ai import Agent
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.usage import UsageLimits

from .agent import build_agent, current_user
from .auth import NotAllowed, NotSignedIn, User, UserTokenVerifier
from .ledger import Ledger
from .settings import Settings

log = logging.getLogger("chess_chat")


class PerUserRate:
    """At most `per_minute` chats per user in any 60 seconds (in memory: one replica)."""

    def __init__(self, per_minute: int, clock=time.monotonic):
        self.per_minute, self.clock = per_minute, clock
        self._seen: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, oid: str) -> bool:
        now = self.clock()
        with self._lock:
            q = self._seen.setdefault(oid, deque())
            while q and now - q[0] >= 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


def problem(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse({"code": code, "detail": detail}, status_code=status)


def create_app(settings: Settings | None = None, *, ledger: Ledger | None = None, agent: Agent | None = None,
               verifier: UserTokenVerifier | None = None) -> FastAPI:
    settings = settings or Settings()
    ledger = ledger or Ledger(settings.ledger_path)
    verifier = verifier or UserTokenVerifier(settings)
    rate = PerUserRate(settings.chats_per_minute)
    unavailable = None
    if agent is None:
        try:
            agent = build_agent(settings, ledger)
        except Exception as e:  # noqa: BLE001  e.g. no model credential yet: run, but say why chat is off
            unavailable = f"{type(e).__name__}: {e}"
            log.error("chat disabled", extra={"reason": unavailable})

    app = FastAPI(title="Lichess lakehouse chat", docs_url=None, redoc_url=None, openapi_url=None)

    def signed_in(authorization: Annotated[str | None, Header()] = None) -> User:
        return verifier.verify(authorization)

    @app.exception_handler(NotSignedIn)
    def not_signed_in(request: Request, exc: NotSignedIn) -> JSONResponse:
        return problem(401, "sign_in", str(exc))

    @app.exception_handler(NotAllowed)
    def not_allowed(request: Request, exc: NotAllowed) -> JSONResponse:
        return problem(403, "not_allowed", str(exc))

    def budget(user: User) -> dict:
        total, mine = ledger.spent(), ledger.spent(user.oid)
        left = max(0.0, min(settings.daily_cap_usd - total, settings.user_daily_cap_usd - mine))
        return {"spent_today_usd": round(mine, 4), "left_today_usd": round(left, 4)}

    @app.get("/api/v1/chat/status")
    def status(user: Annotated[User, Depends(signed_in)]) -> dict:
        return {"user": user.name, "available": unavailable is None, "model": settings.model, **budget(user)}

    @app.post("/api/v1/chat")
    async def chat(request: Request, user: Annotated[User, Depends(signed_in)]) -> Response:
        if unavailable is not None or agent is None:
            return problem(503, "unavailable", "the chat isn't configured yet")
        if budget(user)["left_today_usd"] <= 0:
            return problem(429, "daily_budget", "today's chat budget is used up; it resets at midnight UTC")
        if not rate.allow(user.oid):
            return problem(429, "rate_limit", f"at most {settings.chats_per_minute} questions a minute")
        body = await request.body()
        if len(body) > 512_000:
            return problem(413, "too_large", "the conversation is too long; start a new one")
        current_user.set(user.oid)
        log.info("chat", extra={"oid": user.oid, "bytes": len(body)})
        return await VercelAIAdapter.dispatch_request(
            request,
            agent=agent,
            usage_limits=UsageLimits(request_limit=settings.max_requests_per_run,
                                     tool_calls_limit=settings.max_tool_calls_per_run),
        )

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok\n")

    return app
