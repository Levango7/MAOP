{{/*
Expand the name of the chart.
*/}}
{{- define "maop-operator.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully qualified app name.
*/}}
{{- define "maop-operator.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Chart name and version label.
*/}}
{{- define "maop-operator.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels.
*/}}
{{- define "maop-operator.labels" -}}
helm.sh/chart: {{ include "maop-operator.chart" . }}
app.kubernetes.io/name: {{ include "maop-operator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: maop
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- end -}}

{{/*
Selector labels.
*/}}
{{- define "maop-operator.selectorLabels" -}}
app.kubernetes.io/name: {{ include "maop-operator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Service account name.
*/}}
{{- define "maop-operator.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "maop-operator.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Namespace list for RBAC rules. Returns the watched namespace(s) or empty
(meaning all namespaces) depending on .Values.controller.watchNamespaces.
*/}}
{{- define "maop-operator.watchNamespaces" -}}
{{- if .Values.controller.watchNamespaces -}}
{{- join "," .Values.controller.watchNamespaces -}}
{{- end -}}
{{- end -}}