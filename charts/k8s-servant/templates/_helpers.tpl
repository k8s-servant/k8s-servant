{{/*
Helpers shared by all templates. Names are fixed to "k8s-servant" on purpose:
the Workload Identity federated credential names the ServiceAccount
(system:serviceaccount:k8s-servant:k8s-servant), so it must not drift with the
release name.
*/}}

{{/* The application's name, used for objects and labels. */}}
{{- define "k8s-servant.name" -}}
k8s-servant
{{- end }}

{{/* Chart name and version, for the helm.sh/chart label. */}}
{{- define "k8s-servant.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Labels that select the agent's pod. Never change them: a Deployment's selector is immutable. */}}
{{- define "k8s-servant.selectorLabels" -}}
app.kubernetes.io/name: {{ include "k8s-servant.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Labels on every object: selector labels, chart version and the site labels from values. */}}
{{- define "k8s-servant.labels" -}}
{{ include "k8s-servant.selectorLabels" . }}
helm.sh/chart: {{ include "k8s-servant.chart" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.labels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/* The ServiceAccount's name. */}}
{{- define "k8s-servant.serviceAccountName" -}}
{{- .Values.serviceAccount.name | default "k8s-servant" }}
{{- end }}

{{/*
Names the chart sets itself: in-cluster paths, and the files that hold credentials.
Neither appConfig nor extraEnv may override them.
*/}}
{{- define "k8s-servant.fixedEnv" -}}
{{- $fixed := dict
  "KNOWLEDGE_DIR" "/knowledge"
  "DATA_DIR" "/data"
  "HEARTBEAT_FILE" "/tmp/heartbeat"
  "MCP_URL" "http://127.0.0.1:8080/mcp"
  "KUBECTL_PATH" "/usr/local/bin/kubectl"
  "FOUNDRY_API_KEY_FILE" ""
  "TEAMS_WEBHOOK_URL_FILE" "" -}}
{{- if .Values.llmApiKey.existingSecret }}
{{- $_ := set $fixed "FOUNDRY_API_KEY_FILE" "/var/run/secrets/llm/api-key" }}
{{- end }}
{{- if .Values.teamsWebhook.existingSecret }}
{{- $_ := set $fixed "TEAMS_WEBHOOK_URL_FILE" "/var/run/secrets/teams/webhook-url" }}
{{- end }}
{{- toYaml $fixed }}
{{- end }}

{{/*
The agent's environment variables, rendered as ConfigMap data. Chart-set names cannot
be overridden, and credentials may never be plain settings: the API key and the Teams
webhook URL come only from Secrets mounted as files.
*/}}
{{- define "k8s-servant.appConfig" -}}
{{- $fixed := include "k8s-servant.fixedEnv" . | fromYaml }}
{{- $required := list "CLUSTER_NAME" "FOUNDRY_ENDPOINT" "FOUNDRY_DEPLOYMENT" }}
{{- range $required }}
{{- if not (toString (index $.Values.appConfig .) ) }}
{{- fail (printf "appConfig.%s is required" .) }}
{{- end }}
{{- end }}
{{- $config := dict }}
{{- range $key, $value := .Values.appConfig }}
{{- if hasKey $fixed $key }}
{{- fail (printf "appConfig.%s is set by the chart and cannot be overridden" $key) }}
{{- end }}
{{- if eq $key "FOUNDRY_API_KEY" }}
{{- fail "appConfig.FOUNDRY_API_KEY is not allowed: the API key is a credential and comes only from a Secret (llmApiKey.existingSecret)" }}
{{- end }}
{{- if contains "WEBHOOK_URL" $key }}
{{- fail (printf "appConfig.%s is not allowed: the Teams webhook URL is a credential and comes only from a Secret (teamsWebhook.existingSecret)" $key) }}
{{- end }}
{{- if ne (toString $value) "" }}
{{- $_ := set $config $key (toString $value) }}
{{- end }}
{{- end }}
{{- if not .Values.teamsWebhook.existingSecret }}
{{- $_ := set $config "TEAMS_POST_MODE" "off" }}
{{- end }}
{{- range $key, $value := $fixed }}
{{- if $value }}
{{- $_ := set $config $key $value }}
{{- end }}
{{- end }}
{{- toYaml $config }}
{{- end }}

{{/*
extraEnv, checked: no chart-set name, and no credential as a plain value (valueFrom,
e.g. a secretKeyRef, is fine).
*/}}
{{- define "k8s-servant.extraEnv" -}}
{{- $fixed := include "k8s-servant.fixedEnv" . | fromYaml }}
{{- range .Values.extraEnv }}
{{- if hasKey $fixed .name }}
{{- fail (printf "extraEnv %s is set by the chart and cannot be overridden" .name) }}
{{- end }}
{{- if and (hasKey . "value") (regexMatch "(?i)API_KEY|WEBHOOK_URL|TOKEN|PASSWORD|SECRET" .name) }}
{{- fail (printf "extraEnv %s looks like a credential: use valueFrom (e.g. secretKeyRef), not a plain value" .name) }}
{{- end }}
{{- end }}
{{- toYaml .Values.extraEnv }}
{{- end }}
