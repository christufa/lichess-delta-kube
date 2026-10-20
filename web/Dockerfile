# lichess-web: the public dashboard (#67), built by .github/workflows/images.yml.
#   test     lint, typecheck and unit tests (the Playwright tests run in ci.yml)
#   runtime  the static build on unprivileged nginx (uid 101, port 8080, read-only root fs)
FROM node:24.22.0-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM deps AS source
COPY . .

# ---- tests: docker build --target test . ----
FROM source AS test
RUN npm run lint && npm run typecheck && npm test

FROM source AS build
RUN npm run build

# ---- runtime: static files only, no Node ----
FROM nginxinc/nginx-unprivileged:1.31.7-alpine-slim AS runtime
COPY nginx/nginx.conf nginx/security-headers.conf /etc/nginx/
COPY --from=build /app/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
# Skip the image's entrypoint scripts: they rewrite config, which the read-only root
# filesystem doesn't allow, and nginx.conf is already final.
ENTRYPOINT ["nginx", "-g", "daemon off;"]
