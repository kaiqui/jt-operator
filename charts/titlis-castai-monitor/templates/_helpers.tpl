{{/*
Nome base do chart
*/}}
{{- define "castai-monitor.name" -}}
titlis-castai-monitor
{{- end }}

{{/*
Nome completo do release
*/}}
{{- define "castai-monitor.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
Labels padrão
*/}}
{{- define "castai-monitor.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "castai-monitor.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
ServiceAccount name
*/}}
{{- define "castai-monitor.serviceAccountName" -}}
{{- include "castai-monitor.fullname" . -}}
{{- end -}}
