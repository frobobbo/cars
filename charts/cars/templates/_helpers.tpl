{{- define "cars.name" -}}
cars
{{- end }}

{{- define "cars.fullname" -}}
{{ .Release.Name }}
{{- end }}

{{- define "cars.labels" -}}
app.kubernetes.io/name: {{ include "cars.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "cars.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cars.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
