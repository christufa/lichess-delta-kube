# chess-web

The public dashboard (#67) at `https://chess.generops.dev/`: the Lichess game history month
by month, from the read-only data API (`/api/v1`, [api/](../api/README.md)) on the same
origin. No sign-in.

- **Overview:** a hero with the whole lake in three numbers (games, months loaded, White's
  win rate), then games and players per month by speed (gaps in the loaded months shaded),
  the speed mix, how games end (white / draw / black) by speed, the rating distribution for
  a month and speed (median marked), and the headline numbers with sparklines for the last
  month of the range.
- **Openings:** the most played openings for a month range, speed and 200-point rating band,
  with each one's results, three highlights (most played, best for White, most drawish) and
  a sort by games or by result share (the # stays the popularity rank).
- Filters live in the URL (`/?from=2015-01&to=2015-12&metric=players`,
  `/openings?speed=blitz&band=1600`), so every view can be shared.

The opening explorer (board) and the chat panel from #67 are not built yet.

## Design

Dark is the designed default; light follows the OS setting (no toggle). Colors are tokens
in `src/index.css` (shadcn tokens, a brand gradient, the chart palette). The font is Geist
and Geist Mono, self-hosted from `@fontsource-variable` (bundled by Vite, so the CSP's
`font-src 'self'` holds). Motion:

- The hero's aurora is WebGL (React Bits Aurora), in its own lazy chunk; it pauses off
  screen and draws a single still frame with reduced motion.
- The headline words blur in (BlurText), counters roll to their values when they scroll
  into view (CountUp), cards light up under the pointer or keyboard focus (SpotlightCard),
  the badge text shimmers (ShinyText).
- Cards and rows rise in with staggered CSS animations, bars grow in, sparklines draw
  themselves; ECharts animate on first sight (`src/components/echart.tsx` waits for the
  chart to scroll into view); page changes from the nav cross-fade with the View
  Transitions API.
- `prefers-reduced-motion: reduce` switches all of it off: a global CSS rule for CSS
  animations, transitions and view transitions, and each JS effect checks it (ECharts
  gets `animation: false`). The e2e tests run axe once the animations have finished, and a
  reduced-motion test checks that no CSS animation runs.

The layout leaves room for the chat panel (#69): `Layout` puts the page in a column next
to an empty slot for an `<aside>` drawer.

### Third-party components and licenses

| What | License | Where |
|---|---|---|
| [React Bits](https://reactbits.dev): Aurora, BlurText, CountUp, ShinyText, SpotlightCard | MIT + Commons Clause (use in a site is fine; selling or redistributing the components themselves is not) | copied into `src/components/reactbits` (adapted; notice in its `LICENSE.md`) |
| [motion](https://motion.dev) 12 (BlurText, CountUp) | MIT | npm, pinned |
| Geist / Geist Mono (`@fontsource-variable`) | SIL OFL 1.1 | npm, pinned, bundled |

No runtime CDN: everything ships from the same origin.

## Stack

React 19, TypeScript (strict), Vite, TanStack Router (code-based routes, one chunk per page)
and TanStack Query, Tailwind CSS v4 with shadcn/ui components (`src/components/ui`, added
with the shadcn CLI), ECharts (tree-shaken, SVG renderer, its own lazy chunk), motion. Charts take their colors from CSS
variables in `src/index.css`, follow the OS light/dark setting, have an HTML legend, and
show their numbers as a table behind "Show the numbers".

- **API client:** generated from [`api/openapi.json`](../api/openapi.json) by
  [@hey-api/openapi-ts](https://heyapi.dev) into `src/api/generated` (typed SDK + TanStack
  Query options) and committed. CI regenerates it and fails if it differs, so the dashboard
  and the API can't drift.
- **Caching:** the lake changes monthly, so queries stay fresh for 6 hours and are not
  refetched on focus; 429, 5xx and network errors are retried twice, other errors are shown
  with a "Try again" button.

## Develop (PowerShell)

Needs Node 24 (`.github/workflows/ci.yml` pins the exact version).

```powershell
cd web
npm ci
npm run dev                  # http://localhost:5173, /api proxied to https://chess.generops.dev
$env:CHESS_API_URL = "http://localhost:8000"; npm run dev   # or to a port-forwarded chess-api
npm run generate             # after api/openapi.json changes; commit src/api/generated
npm run lint
npm run typecheck
npm test                     # Vitest: data shaping, URL params, the API client
npm run build
npx playwright install chromium
npm run e2e                  # Playwright + axe on the build, API mocked (e2e/fixtures.ts), desktop and 375 px
```

## Image

`Dockerfile` stages: `test` runs lint, typecheck and unit tests; `runtime` is the static
build on `nginxinc/nginx-unprivileged` (uid 101, port 8080, `/healthz`), with
[`nginx/nginx.conf`](nginx/nginx.conf): client-side routes fall back to `index.html`,
hashed assets are cached for a year, `index.html` never, `/api/` is a 404 (cloudflared
routes it to chess-api first), and every response has a strict Content-Security-Policy.
It runs with a read-only root filesystem; nginx writes only to `/tmp`. CI builds it as
`lichess-web` (`.github/workflows/images.yml`): the `lichess-*` name means Kyverno checks its
cosign signature at admission.

```powershell
docker build --target test web
docker build --target runtime -t lichess-web:dev web
docker run --rm --read-only --tmpfs /tmp -p 8080:8080 lichess-web:dev   # http://localhost:8080 (no API behind it)
$env:E2E_BASE_URL = "http://localhost:8080"; npm run e2e               # the e2e tests against the container
```

## In the cluster

Argo CD deploys `deploy/charts/chess-web` (ApplicationSet `deploy/apps/chess-web.yaml`) into
namespace `chess-web` (Pod Security `restricted`). Network: only cloudflared may connect
(port 8080) and the pods have no egress at all (`deploy/network-policies/chess-web.yaml`).
The public route is in `infra/gitops/edge.tf` (`public_routes`, after the `/api/` rule).

```powershell
kubectl get pods -n chess-web
kubectl port-forward -n chess-web svc/chess-web 8080:80        # http://localhost:8080
```

A merge to main rolls out on its own once its image is signed: CI records the digest on the
`deploy-state` branch, which Argo CD reads ([deploy/README.md](../deploy/README.md#rolling-out-new-builds-chess-api-chess-web)).
