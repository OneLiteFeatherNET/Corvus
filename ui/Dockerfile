# Management UI: static Nuxt build served by an unprivileged nginx.
# Build from the repository root (the context CI uses):  docker build -f ui/Dockerfile -t corvus-ui .
# Runtime settings come from a mounted /usr/share/nginx/html/config.json and CORVUS_UI_* variables, see
# docs/how-to/run-the-ui.md.

FROM node:26-alpine3.24 AS build
WORKDIR /app
RUN corepack enable
COPY ui/package.json ui/pnpm-lock.yaml ui/pnpm-workspace.yaml ./
# `nuxt prepare` (postinstall) needs the sources, so install without scripts first.
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY ui/ ./
RUN pnpm exec nuxt generate && node docker/postprocess.mjs .output/public

FROM nginxinc/nginx-unprivileged:1.30.6-alpine
COPY --from=build /app/.output/public /usr/share/nginx/html
COPY --from=build /app/.output/csp-script-hashes.txt /etc/corvus-ui/csp-script-hashes.txt
COPY ui/docker/default.conf.template /etc/nginx/templates/default.conf.template
# *.envsh is sourced by the entrypoint (exports reach envsubst); it only runs when executable.
COPY --chmod=0755 ui/docker/15-corvus-ui.envsh /docker-entrypoint.d/15-corvus-ui.envsh
# Placeholder so the include in default.conf.template never fails, even if the script is skipped.
RUN : > /tmp/corvus-api-proxy.conf
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
