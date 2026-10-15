{{- define "corvus.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "corvus.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "corvus.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "corvus.selectorLabels" -}}
app.kubernetes.io/name: {{ include "corvus.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "corvus.labels" -}}
helm.sh/chart: {{ include "corvus.chart" . }}
{{ include "corvus.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: corvus
{{- end }}

{{/* Call with (dict "root" $ "component" "api") */}}
{{- define "corvus.componentSelectorLabels" -}}
{{ include "corvus.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "corvus.componentLabels" -}}
{{ include "corvus.labels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/* Labels of the pods of a component: component labels plus podLabels. */}}
{{- define "corvus.podLabels" -}}
{{ include "corvus.componentLabels" . }}
{{- with .root.Values.podLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{- define "corvus.image" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) }}
{{- end }}

{{- define "corvus.uiImage" -}}
{{- printf "%s:%s" .Values.uiImage.repository (default .Chart.AppVersion .Values.uiImage.tag) }}
{{- end }}

{{- define "corvus.jobsImage" -}}
{{- printf "%s:%s" .Values.api.jobs.image.repository (default .Chart.AppVersion .Values.api.jobs.image.tag) }}
{{- end }}

{{/* ServiceAccount name of a component. Call with (dict "root" $ "component" "api") */}}
{{- define "corvus.serviceAccountName" -}}
{{- if .root.Values.serviceAccount.create -}}
{{- printf "%s-%s" (include "corvus.fullname" .root) .component }}
{{- else -}}
default
{{- end -}}
{{- end }}

{{/* Scheduling and pull settings shared by every pod. Call with the root context. */}}
{{- define "corvus.podCommon" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
securityContext:
  {{- toYaml .Values.podSecurityContext | nindent 2 }}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.topologySpreadConstraints }}
topologySpreadConstraints:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{- define "corvus.tmpVolume" -}}
- name: tmp
  emptyDir:
    sizeLimit: {{ .Values.tmpSizeLimit }}
{{- end }}

{{- define "corvus.tmpMount" -}}
- name: tmp
  mountPath: /tmp
{{- end }}

{{- define "corvus.secretEnv" -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ .secret }}
      key: {{ .key }}
{{- end }}

{{/* Either one Secret key with the full DSN, or separate libpq settings (CloudNativePG style). */}}
{{- define "corvus.pgEnv" -}}
{{- $db := .Values.database }}
{{- if $db.host }}
- name: CORVUS_PG_DSN
  value: "postgresql://"
- name: PGHOST
  value: {{ $db.host | quote }}
- name: PGPORT
  value: {{ $db.port | quote }}
- name: PGDATABASE
  value: {{ required "database.name is required with database.host" $db.name | quote }}
{{- if $db.userKey }}
{{ include "corvus.secretEnv" (dict "name" "PGUSER" "secret" $db.existingSecret "key" $db.userKey) }}
{{- else }}
- name: PGUSER
  value: {{ required "database.user or database.userKey is required with database.host" $db.user | quote }}
{{- end }}
{{ include "corvus.secretEnv" (dict "name" "PGPASSWORD" "secret" $db.existingSecret "key" (required "database.passwordKey is required with database.host" $db.passwordKey)) }}
{{- else }}
{{ include "corvus.secretEnv" (dict "name" "CORVUS_PG_DSN" "secret" $db.existingSecret "key" $db.dsnKey) }}
{{- end }}
{{- end }}

{{/* Bucket (and endpoint) from values, or from the ConfigMap an ObjectBucketClaim creates. */}}
{{- define "corvus.s3Env" -}}
{{- $cm := .Values.s3.bucketConfigMap }}
{{- if $cm }}
- name: CORVUS_S3_BUCKET
  valueFrom:
    configMapKeyRef:
      name: {{ $cm }}
      key: BUCKET_NAME
{{- if not .Values.s3.endpointUrl }}
- name: CORVUS_S3_HOST
  valueFrom:
    configMapKeyRef:
      name: {{ $cm }}
      key: BUCKET_HOST
- name: CORVUS_S3_PORT
  valueFrom:
    configMapKeyRef:
      name: {{ $cm }}
      key: BUCKET_PORT
- name: CORVUS_S3_ENDPOINT_URL
  value: "http://$(CORVUS_S3_HOST):$(CORVUS_S3_PORT)"
{{- end }}
{{- else }}
- name: CORVUS_S3_BUCKET
  value: {{ .Values.s3.bucket | quote }}
{{- end }}
{{- with .Values.s3.endpointUrl }}
- name: CORVUS_S3_ENDPOINT_URL
  value: {{ . | quote }}
{{- end }}
- name: CORVUS_S3_REGION
  value: {{ .Values.s3.region | quote }}
{{ include "corvus.secretEnv" (dict "name" "CORVUS_S3_ACCESS_KEY" "secret" .Values.s3.existingSecret "key" .Values.s3.accessKeyKey) }}
{{ include "corvus.secretEnv" (dict "name" "CORVUS_S3_SECRET_KEY" "secret" .Values.s3.existingSecret "key" .Values.s3.secretKeyKey) }}
{{- end }}

{{/* Call with (dict "root" $ "credsKey" <key in nats.creds.existingSecret>). */}}
{{- define "corvus.natsEnv" -}}
- name: CORVUS_NATS_URL
  value: {{ .root.Values.nats.url | quote }}
- name: CORVUS_NATS_STREAM
  value: {{ .root.Values.nats.stream | quote }}
- name: CORVUS_NATS_STREAM_REPLICAS
  value: {{ .root.Values.nats.streamReplicas | quote }}
{{- if .root.Values.nats.creds.existingSecret }}
- name: CORVUS_NATS_CREDS_FILE
  value: /etc/corvus/nats/nats.creds
{{- end }}
{{- end }}

{{/* Read-only mount and volume of one component's NATS credentials file (when nats.creds is set). */}}
{{- define "corvus.natsCredsMount" -}}
{{- if .Values.nats.creds.existingSecret }}
- name: nats-creds
  mountPath: /etc/corvus/nats
  readOnly: true
{{- end }}
{{- end }}

{{/* Call with (dict "root" $ "credsKey" ...): only this component's key is mounted. */}}
{{- define "corvus.natsCredsVolume" -}}
{{- if .root.Values.nats.creds.existingSecret }}
- name: nats-creds
  secret:
    secretName: {{ .root.Values.nats.creds.existingSecret }}
    defaultMode: 0440
    items:
      - key: {{ .credsKey }}
        path: nats.creds
{{- end }}
{{- end }}

{{/* Origin (scheme://host[:port]) of an absolute URL; empty for anything else. */}}
{{- define "corvus.origin" -}}
{{- regexFind "^https?://[^/?#]+" (toString .) -}}
{{- end }}

{{/* Upstream the UI proxies /api/ to: ui.apiUpstream, else the in-cluster api Service. */}}
{{- define "corvus.uiApiUpstream" -}}
{{- if .Values.ui.apiUpstream -}}
{{- .Values.ui.apiUpstream -}}
{{- else if and .Values.ui.proxyApi .Values.api.enabled -}}
{{- printf "http://%s-api:%v" (include "corvus.fullname" .) .Values.api.port -}}
{{- end -}}
{{- end }}

{{/* The UI's config.json: ui.config with basePath taken from ui.basePath. */}}
{{- define "corvus.uiConfig" -}}
{{- mustMergeOverwrite (deepCopy .Values.ui.config) (dict "basePath" .Values.ui.basePath) | toPrettyJson -}}
{{- end }}

{{/* Plain extra env map. Call with the map. */}}
{{- define "corvus.extraEnv" -}}
{{- range $k, $v := . }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end }}

{{/* Ingress spec. Call with (dict "cfg" .Values.api.ingress "svc" "name" "port" 8080) */}}
{{- define "corvus.ingressSpec" -}}
{{- with .cfg.className }}
ingressClassName: {{ . }}
{{- end }}
{{- with .cfg.tls }}
tls:
  {{- toYaml . | nindent 2 }}
{{- end }}
rules:
  {{- $svc := .svc }}
  {{- $port := .port }}
  {{- range .cfg.hosts }}
  - host: {{ .host | quote }}
    http:
      paths:
        {{- range .paths }}
        - path: {{ .path }}
          pathType: {{ .pathType | default "Prefix" }}
          backend:
            service:
              name: {{ $svc }}
              port:
                number: {{ $port }}
        {{- end }}
  {{- end }}
{{- end }}
