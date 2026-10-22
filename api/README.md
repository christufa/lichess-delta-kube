# chess-api

The read-only data API over the gold tables (`lake.gold.*`) for the public dashboard (#66).
Python 3.13, FastAPI, Pydantic v2, managed with [uv](https://docs.astral.sh/uv/).

- **Data:** Trino over HTTPS (private CA), signed in as service account `chess-api` with a
  projected token (audience `trino`). Trino's rules (`deploy/apps/trino.yaml`) let it read
  `lake.gold` and nothing else. Gold counts each game once, so sums over months, speeds and
  rating bands are game counts.
- **Contract:** [`openapi.json`](openapi.json) is generated from the code and committed. CI
  fails if they differ; the dashboard's TypeScript client (#67) is generated from it.
- **Caching:** each response is kept in-process for an hour (gold changes monthly;
  concurrent misses run one query) and sent with `Cache-Control`, `ETag` and `X-Cache`;
  `If-None-Match` gets a 304.
- **Rate limit:** token bucket per client IP (`CF-Connecting-IP` from cloudflared), 60 at
  once, refilled at 5/s, per replica; over it: 429 with `Retry-After`.
- **Telemetry:** JSON logs on stdout with trace ids; OpenTelemetry traces (FastAPI, the
  Trino client's HTTP calls, one span per query), exported over OTLP/HTTP when
  `OTEL_EXPORTER_OTLP_ENDPOINT` is set (no collector in the cluster yet).
- **Probes:** `/healthz` (process up), `/readyz` (Trino answers `SELECT 1`, re-checked at
  most every 30 s).
- **Internal:** `GET /internal/v1/costs?months=3`: the project's daily Azure cost by service
  (`lake.ops.azure_cost`) for the Grafana workflows dashboard (#31).
  - It isn't public in three ways: cloudflared routes only `/api/`, the network policy lets only Grafana in, and a request carrying `CF-Connecting-IP` gets a 404.
  - It's left out of `openapi.json`, so it's not part of the dashboard's contract.
  - Trino lets the `chess-api` user read that one `ops` table besides `gold`.

## Endpoints

Public routes are under `/api` (the path cloudflared will forward from
`https://chess.generops.dev/api`, #45). Interactive docs: `/api/docs`.

| Route | Parameters | Returns |
|---|---|---|
| `GET /api/v1/months` | | months with data |
| `GET /api/v1/summary` | `from`, `to` (YYYY-MM), `speed` (repeatable, incl. `all`) | games, players, results, bot games, avg rating per month × speed |
| `GET /api/v1/rating-distribution` | `month`, `speed` (repeatable) | player-games and players per 50-point bucket |
| `GET /api/v1/openings` | `from`, `to`, `speed`, `rating_band` (multiple of 200), `limit` (≤ 100) | top openings over the range, with results and the selection's total |
| `GET /api/v1/explorer` | `fen` (default: start), `from`, `to` (optional), `speed`, `rating_band` (0/1200/1600/2000/2400) | moves from a position with results, average rating, top-rated example game (`lake.gold.position_moves`; key = first three FEN fields) |

Errors: 400 (`from` after `to`), 422 (invalid parameter), 429, 503 (Trino unavailable);
bodies are `{"detail": ...}`.

## Develop (PowerShell)

```powershell
cd api
uv sync
uv run pytest                              # Trino is faked (SQLite with gold-shaped tables)
uv run ruff check .
uv run python -m chess_api.openapi         # regenerate openapi.json after changing the API; commit it
uv run python -m chess_api.openapi --check # what CI runs
```

Settings are environment variables with prefix `CHESS_API_` (see
[`settings.py`](src/chess_api/settings.py)); the defaults are the cluster's.

## Image

`Dockerfile` stages: `test` runs the tests, `runtime` is the virtualenv on
`python:3.13-slim`, non-root, read-only root filesystem. CI builds it as `lichess-api`
(`.github/workflows/images.yml`): the `lichess-*` name means Kyverno checks its cosign
signature at admission.

```powershell
docker build --target test api
docker build --target runtime -t lichess-api:dev api
```

## In the cluster

Argo CD deploys `deploy/charts/chess-api` (ApplicationSet `deploy/apps/chess-api.yaml`)
into namespace `chess-api`. Network: only cloudflared may connect (port 8000), and the pods
may only reach Trino and DNS (`deploy/network-policies/chess-api.yaml`).

```powershell
kubectl get pods -n chess-api
kubectl exec -n chess-api deploy/chess-api -- python -m chess_api.access_check  # gold yes, curated/raw no
kubectl port-forward -n chess-api svc/chess-api 8000:80                          # http://localhost:8000/api/docs
```

A merge to main rolls out on its own once its image is signed: CI records the digest on the
`deploy-state` branch, which Argo CD reads ([deploy/README.md](../deploy/README.md#rolling-out-new-builds-chess-api-chess-web)).
