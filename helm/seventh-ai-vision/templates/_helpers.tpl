{{/*
Expand the name of the chart.
*/}}
{{- define "seventh-ai-vision.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "seventh-ai-vision.fullname" -}}
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

{{/*
Create chart label value.
*/}}
{{- define "seventh-ai-vision.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to all resources.
*/}}
{{- define "seventh-ai-vision.labels" -}}
helm.sh/chart: {{ include "seventh-ai-vision.chart" . }}
{{ include "seventh-ai-vision.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels (used in matchLabels and Service selectors).
*/}}
{{- define "seventh-ai-vision.selectorLabels" -}}
app.kubernetes.io/name: {{ include "seventh-ai-vision.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Service account name.
*/}}
{{- define "seventh-ai-vision.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "seventh-ai-vision.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Database URL — uses internal postgres Service when postgres.enabled=true,
otherwise falls back to postgres.externalUrl.
*/}}
{{- define "seventh-ai-vision.databaseUrl" -}}
{{- if .Values.postgres.enabled }}
{{- printf "postgresql+asyncpg://%s:%s@%s-postgres:5432/%s" .Values.postgres.username .Values.postgres.password (include "seventh-ai-vision.fullname" .) .Values.postgres.database }}
{{- else }}
{{- required "postgres.externalUrl is required when postgres.enabled=false" .Values.postgres.externalUrl }}
{{- end }}
{{- end }}

{{/*
Redis URL — uses internal redis Service when redis.enabled=true.
*/}}
{{- define "seventh-ai-vision.redisUrl" -}}
{{- if .Values.redis.enabled }}
{{- printf "redis://%s-redis:6379/0" (include "seventh-ai-vision.fullname" .) }}
{{- else }}
{{- required "redis.externalUrl is required when redis.enabled=false" .Values.redis.externalUrl }}
{{- end }}
{{- end }}

{{/*
MinIO / S3 endpoint URL — uses internal MinIO Service when minio.enabled=true.
*/}}
{{- define "seventh-ai-vision.s3EndpointUrl" -}}
{{- if .Values.minio.enabled }}
{{- printf "http://%s-minio:9000" (include "seventh-ai-vision.fullname" .) }}
{{- else }}
{{- .Values.config.s3EndpointUrl }}
{{- end }}
{{- end }}
