# How to run the management UI

The UI (`ui/`) is a static Nuxt single-page app that talks to the control-plane API
([control-plane-api.md](../reference/control-plane-api.md)). One build serves every deployment: the API address, the
OIDC settings and the base path are read from `config.json` when the page loads, not at build time.

## Run it locally with the dev stack

1. Set `CORVUS_API_DEV_TOKEN` in `deploy/dev/.env.api` (see `.env.api.example`).
2. Start the stack. The `ui` service builds `ui/Dockerfile` and proxies `/api/` to the `api` service, so the browser needs no CORS:

   ```sh
   cd deploy/dev
   docker compose up --build
   ```

3. Open <http://127.0.0.1:8081>, paste the dev token and pick the roles the UI should assume. The roles only decide
   which actions the UI shows; the API enforces the roles of the token.

## Develop the UI

Requires Node 22.12 or newer (`ui/.nvmrc` pins 24) and pnpm (the version is in `package.json`).

```sh
cd ui
pnpm install
pnpm dev          # http://localhost:3000, reads public/config.json
pnpm lint
pnpm typecheck
pnpm test         # vitest, 2 workers
pnpm generate     # static build, as in the container image
```

For `pnpm dev` the API must be reachable from the browser: either point `apiBaseUrl` in `public/config.json` at an API with CORS
enabled (`CORVUS_API_CORS_ORIGINS=http://localhost:3000`, off by default) or at a reverse proxy.

After the API schema changes, regenerate the types:

```sh
uv run --project services python ui/scripts/dump-openapi.py   # writes ui/openapi.json
cd ui && pnpm api:types                                      # writes app/types/api.d.ts
```

## Deploy the container

```sh
docker build -f ui/Dockerfile -t corvus-ui .   # from the repository root
docker run -p 8080:8080 \
  -v "$PWD/config.json:/usr/share/nginx/html/config.json:ro" \
  -e CORVUS_UI_BASE_PATH=/ \
  -e CORVUS_UI_API_ORIGIN=https://corvus.example.org \
  -e CORVUS_UI_OIDC_ORIGIN=https://sso.example.org \
  corvus-ui
```

The image is `nginx-unprivileged` (port 8080, no root). It contains no configuration of its own.

### `config.json`

```json
{
  "apiBaseUrl": "https://corvus.example.org/api/v1",
  "basePath": "/",
  "auth": { "mode": "oidc", "rolesClaim": "roles", "roleMapping": {} },
  "oidc": { "issuer": "https://sso.example.org/realms/olf", "clientId": "corvus-ui", "scopes": "openid profile" }
}
```

| Field | Meaning |
|---|---|
| `apiBaseUrl` | API root including `/api/v1`. An absolute URL (needs CORS on the API) or a path such as `/api/v1` behind a proxy. |
| `basePath` | Path the app is served under. Must equal `CORVUS_UI_BASE_PATH`; it is used for the OIDC redirect URIs. |
| `auth.mode` | `oidc` (Authorization Code with PKCE) or `token` (paste a bearer token, development only). |
| `auth.rolesClaim`, `auth.roleMapping` | Where the roles are in the token and how external names map to `admin`, `data_scientist`, `staff`, `viewer`. Use the same values as the API (`CORVUS_API_ROLES_CLAIM`, `CORVUS_API_ROLE_MAPPING`). |
| `oidc.*` | Issuer, public client id and scopes. Register `<origin><basePath>auth/callback` as redirect URI and `<origin><basePath>` as post-logout URI. Add `offline_access` if the IdP should issue refresh tokens for silent renewal. |

The login is stored in `localStorage` and shared by all tabs; every tab keeps its own view in the URL.

### Container variables

| Variable | Default | Effect |
|---|---|---|
| `CORVUS_UI_BASE_PATH` | `/` | Injected as `<base href>`. Set it when a reverse proxy publishes the UI under a prefix and forwards the request path unchanged, e.g. `/corvus/`. |
| `CORVUS_UI_API_ORIGIN` | empty | Origin added to the CSP `connect-src` when the API is on another origin. |
| `CORVUS_UI_OIDC_ORIGIN` | empty | Origin of the OIDC issuer (discovery and token endpoints) added to `connect-src`. |
| `CORVUS_UI_EXTRA_CONNECT` | empty | More space-separated origins for `connect-src`. |
| `CORVUS_UI_API_UPSTREAM` | empty | If set (e.g. `http://api:8080`), nginx proxies `/api/` to it on the same origin, which avoids CORS. |

Values that are not plain `http(s)` origins are ignored with a warning in the container log.

### Security headers

nginx sends a Content-Security-Policy that allows scripts only from the app itself plus the hashes of the two inline bootstrap
scripts (computed at build time), connections only to `self` and the configured origins, no framing, no objects and a fixed `base-uri`.
Styles allow `unsafe-inline` because Vuetify injects its theme styles at runtime. Also set: `X-Content-Type-Options`,
`X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`. `config.json` is served with `no-store`,
`/_nuxt/` assets are immutable, every other path falls back to `index.html`.

## How the UI uses the API

The UI works against the extended control-plane API ([control-plane-api.md](../reference/control-plane-api.md)) and
does not work around missing endpoints any more:

- **Roles:** after the login the UI calls `GET /me` and shows what the API reports (roles after the API's role
  mapping, display name). The roles read from the token claim (`auth.rolesClaim`, `auth.roleMapping`) are only the
  fallback if `/me` fails.
- **Dashboard:** one request, `GET /summary`, for the counts, the models per stage, the 10 newest jobs and the
  10 newest erasure requests.
- **Erasure requests:** the Players page lists `GET /erasure-requests` (admin, paginated) with the compaction
  status (`compaction_pending`, `compacted_at`). The UI no longer keeps request ids in the browser.
- **Models:** the registry filters (`name`, `version`, `game_context`, `domain`, `stage`) run on the server and need
  an exact match; the detail page loads `GET /models/{name}/{version}`.
- **Pool preview:** while a pool is edited, the UI previews the unsaved filter with `POST /pools/preview`. Requests
  are debounced (500 ms); a newer request aborts the one in flight.
- **Scores:** the Scores page (roles `staff` and `admin`) shows the latest scores of a pseudonym per model and game
  context and the score history as a chart, from `GET /scores/players/{pseudonym}` and `/history`. Scores are
  decision aids; Corvus never sanctions players. The page can be opened with `/scores?pseudonym=<64 hex>`.
- **CORS:** `CORVUS_API_CORS_ORIGINS` lists the browser origins that may call the API (credentials stay off); the
  same-origin proxy (`CORVUS_UI_API_UPSTREAM`) still avoids the need for it.
