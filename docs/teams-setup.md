# MS Teams setup

The agent posts through a Teams **Workflows** webhook (the old "Incoming Webhook"
connectors were retired). The cards look and behave as described in
[reports.md](reports.md#teams-cards).

1. **Webhook.** In the channel, **…** → **Workflows** → template **"Send webhook
   alerts to a channel"** → name it (e.g. *k8s-servant my-cluster*) → **Save**, and copy
   the URL. Add a co-owner to the flow so it does not depend on one person. Reusing the
   webhook of an existing channel (for example the one Alertmanager posts to) also works.
2. **Secret store.** The URL is a password. Keep it in a secret store (for example
   Azure Key Vault, secret `teams-webhook-url` in `my-keyvault`). In the cluster the
   agent reads it from a Kubernetes Secret named in `teamsWebhook.existingSecret`,
   which you create with kubectl, External Secrets or any other tool
   ([how](deployment.md#credentials-bring-your-own-secrets)).
3. **Test.** Load the URL into your shell (see
   [Secrets: local vs cluster](configuration.md#secrets-local-vs-cluster)). `k8s-servant doctor` should
   show `[OK] teams`. Then `TEAMS_POST_MODE=always k8s-servant check --post` posts a
   check card, and `k8s-servant report --post` the morning report.

Cards stay under Teams' size limit: with many findings, details are shortened and
lists end with "… and N more".
