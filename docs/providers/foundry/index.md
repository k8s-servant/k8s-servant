# Microsoft Foundry

[Microsoft Foundry](https://ai.azure.com) (formerly Azure AI Foundry) serves models
inside your own Azure tenant, in the data zone you choose. On AKS the pod signs in with Workload Identity, so there is
no key to manage. Foundry has four pages here:

| Page | Read it when |
|---|---|
| [GPT and other models](gpt.md) | You want a GPT model (or DeepSeek, Llama, Grok, ...) on Foundry: the `.env`, the Helm values and `doctor` |
| [Claude on Foundry](claude.md) | You want Claude, but inside Azure, with the same sign-in and billing |
| [Concepts](concepts.md) | You are new to Foundry: resources, deployments, quota, cost, access, network |
| [Setup guide](setup.md) | You set up the Foundry side step by step: resource, deployment, roles, networking, smoke test |

`scripts/setup-foundry.sh` does the setup guide's steps in one go, and
`scripts/setup-workload-identity.sh` gives the pod its identity. Rules that hold for
every provider, Foundry included, are on the [model providers](../../model-providers.md)
page.
