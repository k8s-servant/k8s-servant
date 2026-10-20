# OpenAI

**Choose it when** you have an OpenAI account (a ChatGPT subscription is not an API
account) and want the shortest path, on a laptop or in any cluster. It uses the Responses
API.

Laptop `.env`:

```bash
LLM_PROVIDER=openai
LLM_MODEL=gpt-5
LLM_API_KEY_FILE=/path/to/openai-api-key   # or LLM_API_KEY=... for a quick test
```

No `LLM_ENDPOINT` is needed (it defaults to `https://api.openai.com/v1`).

Cluster: [`examples/values-openai.yaml`](https://github.com/k8s-servant/k8s-servant/blob/main/examples/values-openai.yaml). Put the key in a
file named `api-key` and create the Secret (this keeps it out of your shell history):

```bash
kubectl create namespace k8s-servant
kubectl -n k8s-servant create secret generic k8s-servant-llm --from-file=./api-key
rm ./api-key
helm install k8s-servant oci://ghcr.io/k8s-servant/charts/k8s-servant \
  -n k8s-servant -f examples/values-openai.yaml
```

```yaml
appConfig:
  CLUSTER_NAME: my-cluster
  LLM_PROVIDER: openai
  LLM_MODEL: gpt-5
  LLM_AUTH: api-key
llmApiKey:
  existingSecret: k8s-servant-llm
```

The chart mounts the Secret as the file `/var/run/secrets/llm/api-key` and sets
`LLM_API_KEY_FILE` for you; the key is never an environment variable. External Secrets,
Sealed Secrets or the Secrets Store CSI driver work just as well, as long as a Secret or
file with the key exists ([deployment.md](../deployment.md#credentials-bring-your-own-secrets)).

Expected `doctor` model line:

```
model: openai / gpt-5 via Responses API, API key (file /var/run/secrets/llm/api-key)
```
