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

{{- /*
The image reference. A digest wins over a tag when one is set, and failure.badImage wins over
both: it asks for a tag that does not exist, which is how this chart produces the one failure in
the taxonomy that has no container state at all to read.

The tag is deliberately not a random string. A reader seeing it in a transcript should be able
to tell at a glance that the pull was MEANT to fail.
*/ -}}
{{- define "deploy-canary.image" -}}
{{- if .Values.failure.badImage -}}
{{ .Values.image.repository }}:no-such-tag-this-pull-must-fail
{{- else if .Values.image.digest -}}
{{ .Values.image.repository }}@{{ .Values.image.digest }}
{{- else -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}
{{- end -}}
