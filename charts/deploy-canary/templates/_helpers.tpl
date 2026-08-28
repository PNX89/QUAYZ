{{- define "deploy-canary.name" -}}
{{ .Release.Name }}-canary
{{- end -}}

{{- define "deploy-canary.labels" -}}
app.kubernetes.io/name: deploy-canary
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "deploy-canary.selector" -}}
app.kubernetes.io/name: deploy-canary
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "deploy-canary.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}@{{ .Values.image.digest }}
{{- else -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}
{{- end -}}
