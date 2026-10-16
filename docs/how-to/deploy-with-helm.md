# How to deploy Corvus with Helm

Deploys the Corvus backend to Kubernetes with the chart in `deploy/helm/corvus`. NATS JetStream, PostgreSQL and an S3
bucket are external dependencies: the chart does not install them, it only references them through values and
existing Secrets.

| Component | Kind | Image command |
|---|---|---|
| migrate | Job, Helm `pre-install,pre-upgrade` hook | `corvus-migrate` |
| ingest | Deployment (+ optional HPA), metrics Service | `corvus-ingest` |
| api | Deployment, Service, optional Ingress, RBAC for jobs | `corvus-api` |
| score | StatefulSet, headless Service | `corvus-score` |
| housekeeping | CronJob (`concurrencyPolicy: Forbid`) | `corvus-housekeeping` |
| compact | CronJob (hourly, `concurrencyPolicy: Forbid`, toggle `compact.enabled`) | `corvus-compact` |
| ui | optional Deployment, Service, Ingress, `config.json` ConfigMap | static nginx image |

## Images and versions

Release-please keeps `version` and `appVersion` in `Chart.yaml` equal to the release version, and the release workflow
publishes `harbor.onelitefeather.dev/onelitefeather/corvus-services` and `.../corvus-ui` with that same version as the
tag (without a leading `v`). The chart defaults every image tag to `appVersion`, so a chart release always deploys the
images of the same release. Override `image.tag` / `uiImage.tag` to pin something else.

## Prerequisites

- Kubernetes 1.28 or newer (the score StatefulSet reads its ordinal from the `apps.kubernetes.io/pod-index` label).
- A JetStream-enabled NATS, PostgreSQL (CloudNativePG works out of the box) and an S3 bucket.
- The Secrets below, created in the release namespace before the first install (the migrate hook runs first).

## Secrets

The chart never contains secret values. Create these Secrets yourself (for example with the External Secrets
Operator or SealedSecrets) and point the values at them:

| Value | Default Secret / key | Content | Used by |
|---|---|---|---|
| `database.existingSecret`, `database.dsnKey` | `corvus-database` / `uri` | libpq DSN incl. password (the CNPG app secret key `uri` fits) | migrate, ingest, api, score, housekeeping, compact |
| `s3.existingSecret`, `s3.accessKeyKey`, `s3.secretKeyKey` | `corvus-s3` / `access-key`, `secret-key` | object store credentials | ingest, score, housekeeping, compact |
| `kek.existingSecret`, `kek.key` | `corvus-kek` / `kek` | key encryption key, base64 of 32 random bytes (`openssl rand -base64 32`) | ingest |
| `pseudonym.existingSecret`, `pseudonym.key` | `corvus-pseudonym` / `key` | pseudonym key shared with the proxies, base64, at least 16 bytes | api (`pseudonym.enabled: false` disables it) |
| `api.extraSecret` (optional) | none | every key becomes an env var, e.g. `CORVUS_API_PLUGIN_TOKENS` (comma separated proxy tokens) | api |
| `api.jobs.envSecret` (optional) | none | S3 / database credentials exposed to training job pods | jobs started by the api |

Raw payload retention is not a secret: `rawRetention.defaultDays` (21) and `rawRetention.maxDays` (365) reach
housekeeping, which applies them, and the api, which validates a pool's `raw_retention_days` against them
(`CORVUS_RAW_RETENTION_DAYS`, `CORVUS_RAW_RETENTION_MAX_DAYS`). The default must not exceed the maximum.

**Database, object store and NATS variants.**

- *CloudNativePG with separate keys:* set `database.host` (for example `corvus-db-rw`), `database.name`,
  `database.port` and `database.passwordKey` (plus `database.userKey`, or a plain `database.user`). The password is
  read from `database.existingSecret` and reaches psycopg as `PGPASSWORD` (user and host as `PGUSER` and `PGHOST`);
  `database.dsnKey` is then ignored.
- *Rook-Ceph ObjectBucketClaim:* the claim's Secret uses the keys `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, so set
  `s3.existingSecret`, `s3.accessKeyKey: AWS_ACCESS_KEY_ID` and `s3.secretKeyKey: AWS_SECRET_ACCESS_KEY`. Its ConfigMap
  (`BUCKET_NAME`, `BUCKET_HOST`, `BUCKET_PORT`) can replace `s3.bucket` and the endpoint: set `s3.bucketConfigMap` to its
  name. `s3.endpointUrl` still wins when set (for example an in-cluster service URL).
- *NATS:* `nats.streamReplicas` (default 1; 3 on a three-node cluster) sets the replicas of the JetStream stream, and an
  existing stream with fewer replicas is updated. `nats.creds.existingSecret` holds one `.creds` file per component
  (`nats.creds.ingestKey`, `nats.creds.scoreKey`, because ingest and score are different NATS users); each pod mounts only
  its own key read-only and gets `CORVUS_NATS_CREDS_FILE`. This needs a services image that supports those settings.

Losing the KEK makes every stored player key, and with it all raw payloads, unreadable. Back it up.

## Install

```sh
helm upgrade --install corvus deploy/helm/corvus -n corvus --create-namespace -f my-values.yaml
```

A minimal `my-values.yaml`:

```yaml
nats:
  url: nats://nats.nats.svc:4222
s3:
  endpointUrl: http://rook-ceph-rgw.rook-ceph.svc:80
  bucket: corvus-lake
api:
  oidc:
    issuer: https://idp.example.org/realms/olf
    audience: corvus
    jwksUrl: https://idp.example.org/realms/olf/protocol/openid-connect/certs
    rolesClaim: realm_access.roles
  ingress:
    enabled: true
    className: nginx
    hosts:
      - host: corvus-api.example.org
        paths:
          - path: /
```

The `values.schema.json` rejects unknown top-level keys and wrong types, and the hardened defaults cannot be switched
off (non-root, read-only root filesystem, no capabilities, `RuntimeDefault` seccomp). `/tmp` is a per-pod emptyDir.

## Scaling

- **ingest** scales horizontally: raise `ingest.replicas` or enable `ingest.autoscaling`. Defaults are 1 to 4 CPUs and
  at most 1 GiB per pod.
- **score** is a StatefulSet. Replica `i` of `n` owns the partitions `p` with `p mod n == i`, so the chart sets
  `CORVUS_SCORE_REPLICAS` from `score.replicas` (the same value that sets the StatefulSet replicas) and
  `CORVUS_SCORE_REPLICA_INDEX` from the pod ordinal. Change the count only through `score.replicas`; never use
  `kubectl scale` or an HPA, because the two values would drift and partitions would be dropped or scored twice.
- **api** training jobs: with `api.jobs.runner: kubernetes` (default) the chart creates a Role that allows
  `create`, `get`, `list` and `watch` on `batch/jobs` and `get` and `list` on `pods` (to report why a job pod died,
  for example `OOMKilled (exit 137)`) in the release namespace and binds it to the api ServiceAccount; that is the
  only component whose pod mounts a ServiceAccount token. Set `api.jobs.image` to the ML image.
  `api.jobs.reconcileIntervalSeconds` (default 30, 0 = off) sets how often the api marks killed or vanished jobs
  failed.

## Optional parts

- `ui.enabled: true` deploys the UI. `ui.config` is rendered as `/usr/share/nginx/html/config.json` and uses the keys
  the UI reads (`apiBaseUrl`, `auth.mode`, `auth.rolesClaim`, `auth.roleMapping`, `oidc.issuer`, `oidc.clientId`,
  `oidc.scopes`, see [run-the-ui.md](run-the-ui.md)); the schema rejects other keys. `basePath` comes from
  `ui.basePath`, which also sets `CORVUS_UI_BASE_PATH`. The container always listens on 8080 (`ui.port` is only the
  Service port) and runs as uid 101 (`ui.podSecurityContext`), the user of the nginx-unprivileged image.
  - **Same origin (default).** `ui.proxyApi: true` makes nginx proxy `/api/` to the in-cluster api Service
    (`CORVUS_UI_API_UPSTREAM`), so `ui.config.apiBaseUrl: /api/v1` needs neither CORS nor an extra CSP entry. With
    `networkPolicy.enabled` the chart lets the UI pods reach the api pods. Set `ui.apiUpstream` to proxy elsewhere.
  - **Separate origin.** Put the absolute URL into `ui.config.apiBaseUrl`, set `ui.proxyApi: false` and list the UI's
    origin in `api.cors.origins` (`CORVUS_API_CORS_ORIGINS`). The origins for the Content-Security-Policy are derived
    from `apiBaseUrl` and `oidc.issuer`; override them with `ui.apiOrigin`, `ui.oidcOrigin` and `ui.extraConnect`.
- `api.jobTokens.existingSecret` (key `api.jobTokens.key`, default `job-tokens`) provides `CORVUS_API_JOB_TOKENS`, the
  comma separated `<sha256 hex of the token>:<job name>` entries of the ML jobs (see
  [control-plane-api.md](../reference/control-plane-api.md)). The jobs themselves need `CORVUS_API_URL` and the plain
  `CORVUS_API_TOKEN`: put both into the Secret named by `api.jobs.envSecret`.
- `networkPolicy.enabled: true` restricts every component to DNS plus the ports in `networkPolicy.egressPorts`
  (NATS 4222, PostgreSQL 5432, HTTP(S), Kubernetes API) and to ingress on its own port.
- `serviceMonitor.enabled: true` creates Prometheus Operator ServiceMonitors for ingest, score and api.

## Deploy with Flux

When the chart is installed from a Git branch, set `reconcileStrategy: Revision` on the chart source. The default,
`ChartVersion`, only produces a new chart artifact when `Chart.yaml`'s version changes, so changes to templates or
values on the branch would silently never be redeployed.

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: corvus
  namespace: flux-system
spec:
  interval: 5m
  url: https://github.com/OneLiteFeatherNET/Corvus
  ref:
    branch: main
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: corvus
  namespace: corvus
spec:
  interval: 10m
  chart:
    spec:
      chart: ./deploy/helm/corvus
      reconcileStrategy: Revision
      sourceRef:
        kind: GitRepository
        name: corvus
        namespace: flux-system
  install:
    remediation:
      retries: 3
  upgrade:
    remediation:
      retries: 3
  values:
    nats:
      url: nats://nats.nats.svc:4222
    s3:
      endpointUrl: http://rook-ceph-rgw.rook-ceph.svc:80
```

Because the chart tracks `main`, `appVersion` (and with it the default image tag) only moves when a release is cut.
Merging a change to the chart without a release redeploys the new templates with the previous images.

## Check the result

```sh
kubectl -n corvus get pods
kubectl -n corvus logs job/corvus-migrate        # only while the hook Job still exists
kubectl -n corvus port-forward svc/corvus-api 8080 &
curl -s localhost:8080/readyz
```

## Validate chart changes

```sh
helm lint deploy/helm/corvus -f deploy/helm/corvus/ci/full-values.yaml
helm template corvus deploy/helm/corvus -f deploy/helm/corvus/ci/full-values.yaml | kubeconform -strict -ignore-missing-schemas
helm unittest deploy/helm/corvus     # needs the helm-unittest plugin
```
