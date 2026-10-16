# Management UI: static Nuxt build served by an unprivileged nginx.
# Build from the repository root (the context CI uses):  docker build -f ui/Dockerfile -t corvus-ui .
# Runtime settings come from a mounted /usr/share/nginx/html/config.json and CORVUS_UI_* variables, see
# docs/how-to/run-the-ui.md.

FROM node:24-alpine3.24 AS build
WORKDIR /app
RUN corepack enable
COPY ui/package.json ui/pnpm-lock.yaml ui/pnpm-workspace.yaml ./
# `nuxt prepare` (postinstall) needs the sources, so install without scripts first.
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY ui/ ./
RUN pnpm exec nuxt generate && node docker/postprocess.mjs .output/public

FROM nginxinc/nginx-unprivileged:1.31.6-alpine
COPY --from=build /app/.output/public /usr/share/nginx/html
COPY --from=build /app/.output/csp-script-hashes.txt /etc/corvus-ui/csp-script-hashes.txt
COPY ui/docker/default.conf.template /etc/nginx/templates/default.conf.template
COPY ui/docker/15-corvus-ui.sh /docker-entrypoint.d/15-corvus-ui.sh
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
