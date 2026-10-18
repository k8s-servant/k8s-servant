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
  "LLM_API_KEY_FILE" ""
  "TEAMS_WEBHOOK_URL_FILE" "" -}}
{{- $credentials := list
  (list "llmApiKey" .Values.llmApiKey "LLM_API_KEY_FILE" "/var/run/secrets/llm/api-key")
  (list "teamsWebhook" .Values.teamsWebhook "TEAMS_WEBHOOK_URL_FILE" "/var/run/secrets/teams/webhook-url") }}
{{- range $credentials }}
{{- $name := index . 0 }}{{- $values := index . 1 }}{{- $env := index . 2 }}{{- $mounted := index . 3 }}
{{- if and $values.existingSecret $values.file }}
{{- fail (printf "%s: set existingSecret or file, not both" $name) }}
{{- end }}
{{- if $values.existingSecret }}
{{- $_ := set $fixed $env $mounted }}
{{- else if $values.file }}
{{- if not (hasPrefix "/" $values.file) }}
{{- fail (printf "%s.file must be an absolute path, got %q" $name $values.file) }}
{{- end }}
{{- $_ := set $fixed $env $values.file }}
{{- end }}
{{- end }}
{{- toYaml $fixed }}
{{- end }}

{{/* Whether Teams posting has a webhook source (a Secret or a file). */}}
{{- define "k8s-servant.teamsEnabled" -}}
{{- if or .Values.teamsWebhook.existingSecret .Values.teamsWebhook.file }}true{{ end }}
{{- end }}

{{/*
The agent's environment variables, rendered as ConfigMap data. Chart-set names cannot
be overridden, and credentials may never be plain settings: the API key and the Teams
webhook URL come only from Secrets mounted as files.
*/}}
{{- define "k8s-servant.appConfig" -}}
{{- $fixed := include "k8s-servant.fixedEnv" . | fromYaml }}
{{- $required := list "CLUSTER_NAME" "LLM_PROVIDER" "LLM_MODEL" }}
{{- if has (toString (index $.Values.appConfig "LLM_PROVIDER")) (list "foundry" "openai-compatible") }}
{{- $required = append $required "LLM_ENDPOINT" }}
{{- end }}
{{- range $required }}
{{- if not (toString (index $.Values.appConfig .) ) }}
{{- fail (printf "appConfig.%s is required" .) }}
{{- end }}
{{- end }}
{{- $renamed := dict
  "FOUNDRY_ENDPOINT" "LLM_ENDPOINT"
  "FOUNDRY_DEPLOYMENT" "LLM_MODEL"
  "FOUNDRY_API_KEY" "LLM_API_KEY (as a Secret: llmApiKey.existingSecret / llmApiKey.file)"
  "FOUNDRY_API_KEY_FILE" "llmApiKey.existingSecret / llmApiKey.file"
  "FOUNDRY_TOKEN_SCOPE" "LLM_TOKEN_SCOPE"
  "FOUNDRY_TIMEOUT_SECONDS" "LLM_TIMEOUT_SECONDS"
  "FOUNDRY_REASONING_EFFORT" "LLM_REASONING_EFFORT" }}
{{- $config := dict }}
{{- range $key, $value := .Values.appConfig }}
{{- if hasKey $fixed $key }}
{{- fail (printf "appConfig.%s is set by the chart and cannot be overridden" $key) }}
{{- end }}
{{- if hasPrefix "FOUNDRY_" $key }}
{{- fail (printf "appConfig.%s is no longer supported: use appConfig.%s (the FOUNDRY_* settings became LLM_*; see docs/model-providers.md)" $key (get $renamed $key | default "LLM_*")) }}
{{- end }}
{{- if contains "API_KEY" $key }}
{{- fail (printf "appConfig.%s is not allowed: the API key is a credential and comes only from a Secret or a mounted file (llmApiKey.existingSecret / llmApiKey.file)" $key) }}
{{- end }}
{{- if contains "WEBHOOK_URL" $key }}
{{- fail (printf "appConfig.%s is not allowed: the Teams webhook URL is a credential and comes only from a Secret or a mounted file (teamsWebhook.existingSecret / teamsWebhook.file)" $key) }}
{{- end }}
{{- if ne (toString $value) "" }}
{{- $_ := set $config $key (toString $value) }}
{{- end }}
{{- end }}
{{- if not (include "k8s-servant.teamsEnabled" .) }}
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
