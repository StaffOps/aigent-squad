{{/*
Common labels for a service.
Usage: {{ include "aigent.labels" (dict "name" $name "root" $) }}
*/}}
{{- define "aigent.labels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/part-of: aigent-squad
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ .root.Chart.Name }}-{{ .root.Chart.Version }}
CostCenter: {{ .root.Values.global.costCenter }}
Environment: {{ .root.Values.global.environment }}
{{- end -}}

{{/*
Selector labels for a service.
Usage: {{ include "aigent.selectorLabels" (dict "name" $name) }}
*/}}
{{- define "aigent.selectorLabels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/part-of: aigent-squad
{{- end -}}

{{/*
Image reference.
Usage: {{ include "aigent.image" $ }}
*/}}
{{- define "aigent.image" -}}
{{ .Values.global.image.repository }}:{{ .Values.global.image.tag | default .Chart.AppVersion }}
{{- end -}}

{{/*
Full name for a service resource.
Usage: {{ include "aigent.fullname" (dict "name" $name "root" $) }}
*/}}
{{- define "aigent.fullname" -}}
{{ .root.Release.Name }}-{{ .name }}
{{- end -}}
