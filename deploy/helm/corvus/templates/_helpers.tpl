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

{{- define "corvus.pgEnv" -}}
{{ include "corvus.secretEnv" (dict "name" "CORVUS_PG_DSN" "secret" .Values.database.existingSecret "key" .Values.database.dsnKey) }}
{{- end }}

{{- define "corvus.s3Env" -}}
- name: CORVUS_S3_BUCKET
  value: {{ .Values.s3.bucket | quote }}
- name: CORVUS_S3_REGION
  value: {{ .Values.s3.region | quote }}
{{- with .Values.s3.endpointUrl }}
- name: CORVUS_S3_ENDPOINT_URL
  value: {{ . | quote }}
{{- end }}
{{ include "corvus.secretEnv" (dict "name" "CORVUS_S3_ACCESS_KEY" "secret" .Values.s3.existingSecret "key" .Values.s3.accessKeyKey) }}
{{ include "corvus.secretEnv" (dict "name" "CORVUS_S3_SECRET_KEY" "secret" .Values.s3.existingSecret "key" .Values.s3.secretKeyKey) }}
{{- end }}

{{- define "corvus.natsEnv" -}}
- name: CORVUS_NATS_URL
  value: {{ .Values.nats.url | quote }}
- name: CORVUS_NATS_STREAM
  value: {{ .Values.nats.stream | quote }}
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
