#!/bin/sh
# Sourced by the nginx entrypoint before the template step (not executable on purpose). Turns the deployment
# settings into the variables used by default.conf.template.
#
#   CORVUS_UI_BASE_PATH     path the app is served under, default "/" (e.g. "/corvus/")
#   CORVUS_UI_API_ORIGIN    origin of the control-plane API, e.g. https://corvus.example.org (empty: same origin)
#   CORVUS_UI_OIDC_ORIGIN   origin of the OIDC issuer (and its token endpoint), e.g. https://sso.example.org
#   CORVUS_UI_API_UPSTREAM  optional upstream (e.g. http://api:8080) that /api/ is proxied to on the same origin
#   CORVUS_UI_EXTRA_CONNECT additional connect-src entries, space separated (optional)

base="${CORVUS_UI_BASE_PATH:-/}"
case "$base" in /*) ;; *) base="/$base" ;; esac
case "$base" in */) ;; *) base="$base/" ;; esac
export CORVUS_UI_BASE_PATH="$base"

# Only plain origins are accepted; anything else could break out of the header.
connect=""
for origin in ${CORVUS_UI_API_ORIGIN:-} ${CORVUS_UI_OIDC_ORIGIN:-} ${CORVUS_UI_EXTRA_CONNECT:-}; do
  case "$origin" in
    http://* | https://* | ws://* | wss://*)
      case "$origin" in
        *[!A-Za-z0-9.:/_-]*) echo "15-corvus-ui: ignoring invalid origin '$origin'" >&2 ;;
        *) connect="$connect $origin" ;;
      esac
      ;;
    *) echo "15-corvus-ui: ignoring invalid origin '$origin'" >&2 ;;
  esac
done
export CORVUS_UI_CONNECT_SRC="${connect# }"

# Optional: proxy /api/ to the API on the same origin, so no CORS and no extra connect-src entry is needed.
proxy=/tmp/corvus-api-proxy.conf
: > "$proxy"
if [ -n "${CORVUS_UI_API_UPSTREAM:-}" ]; then
  case "$CORVUS_UI_API_UPSTREAM" in
    http://* | https://*)
      case "$CORVUS_UI_API_UPSTREAM" in
        *[!A-Za-z0-9.:/_-]*) echo "15-corvus-ui: ignoring invalid upstream" >&2 ;;
        *)
          cat > "$proxy" <<CONF
location /api/ {
    proxy_pass ${CORVUS_UI_API_UPSTREAM};
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-For \$remote_addr;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_read_timeout 60s;
}
CONF
          ;;
      esac
      ;;
    *) echo "15-corvus-ui: ignoring invalid upstream" >&2 ;;
  esac
fi

CORVUS_UI_SCRIPT_HASHES="$(cat /etc/corvus-ui/csp-script-hashes.txt)"
export CORVUS_UI_SCRIPT_HASHES
