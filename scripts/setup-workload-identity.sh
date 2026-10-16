#!/bin/bash
# =============================================================================
# setup-workload-identity.sh
# Creates the Azure identity the k8s-servant pod uses (AKS Workload Identity)
# and gives it exactly the access it needs:
#   - read ONE Key Vault secret: the Teams webhook URL, synced by External
#     Secrets into the agent's own Kubernetes Secret;
#   - call the chat model in the MS Foundry resource.
# This script is the documented way to set the identity up (a prerequisite of
# the deployment, see docs/deployment.md).
#
# What it does (each step is skipped if already done, so it is safe to re-run):
#   1. Ensures the identity's resource group exists (in the cluster's region).
#   2. Creates the user-assigned managed identity.
#   3. Creates the federated credential "<cluster>-<identity>-fed-cred" for
#      system:serviceaccount:<namespace>:<serviceaccount> on the cluster's OIDC
#      issuer, or UPDATES it when the issuer changed (a recreated cluster gets a
#      new issuer, so re-run the script after every recreation).
#      One identity can serve several clusters: run the script once per cluster
#      with the same identity; each cluster gets its own federated credential.
#   4. Key Vault: on an Azure RBAC vault, "Key Vault Secrets User" scoped to the
#      one secret (not the vault). On an access-policy vault that is impossible,
#      so the script stops unless ALLOW_VAULT_WIDE_SECRET_READ=yes is set; then
#      "get" on secrets is set for the whole vault.
#   5. "Cognitive Services OpenAI User" on the Foundry resource (any subscription
#      of the same tenant).
#   6. Prints the Helm values (client ID, tenant ID).
#
# EPHEMERAL_CLUSTER=yes: set it when the cluster and its resource group are
# destroyed and recreated regularly. The script then refuses to put the identity
# into the cluster's resource group (it would block the destroy or be deleted
# with it, changing its client ID), and reminds you to re-run after recreation.
#
# The Key Vault and the Foundry resource are given as resource IDs because they
# usually live in other subscriptions than the cluster:
#   az keyvault show --name <vault> --query id -o tsv
#   az cognitiveservices account show --name <foundry> --resource-group <rg> --query id -o tsv
#
# Usage:
#   ./scripts/setup-workload-identity.sh \
#     <SUBSCRIPTION_NAME> <IDENTITY_RESOURCE_GROUP> <IDENTITY_NAME> \
#     <CLUSTER_RESOURCE_GROUP> <CLUSTER_NAME> <KEYVAULT_ID> <FOUNDRY_ID> \
#     [KEYVAULT_SECRET_NAME] [SERVICE_ACCOUNT_NAMESPACE] [SERVICE_ACCOUNT_NAME]
#
# Example:
#   ./scripts/setup-workload-identity.sh \
#     "My Subscription" "my-identity-rg" "k8s-servant-identity" \
#     "my-cluster-rg" "my-cluster" \
#     "$(az keyvault show --name my-keyvault --query id -o tsv)" \
#     "$(az cognitiveservices account show --name my-foundry \
#          --resource-group my-foundry-rg --query id -o tsv)"
#
# Required permissions: Contributor on the identity's resource group, and Owner
# or User Access Administrator (role assignments) on the Key Vault and the
# Foundry resource; for an access-policy vault, permission to change its policies.
# =============================================================================
set -euo pipefail

SUBSCRIPTION_NAME=${1:-""}
IDENTITY_RESOURCE_GROUP=${2:-""}
IDENTITY_NAME=${3:-""}
CLUSTER_RESOURCE_GROUP=${4:-""}
CLUSTER_NAME=${5:-""}
KEYVAULT_ID=${6:-""}
FOUNDRY_ID=${7:-""}
KEYVAULT_SECRET_NAME=${8:-"alertmanager-teams-webhook-url"}
SERVICE_ACCOUNT_NAMESPACE=${9:-"k8s-servant"}
SERVICE_ACCOUNT_NAME=${10:-"k8s-servant"}

KV_ROLE="Key Vault Secrets User"
FOUNDRY_ROLE="Cognitive Services OpenAI User"
KV_ID_PATTERN='^/subscriptions/([^/]+)/resourceGroups/([^/]+)/providers/Microsoft\.KeyVault/vaults/([^/]+)$'
FOUNDRY_ID_PATTERN='^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/[^/]+$'

# ---------------------------------------------------------------------------
# Validation (before any Azure call)
# ---------------------------------------------------------------------------
ERRORS=()

[[ -z "$SUBSCRIPTION_NAME" ]]       && ERRORS+=("SUBSCRIPTION_NAME (arg 1) is required (the cluster's subscription)")
[[ -z "$IDENTITY_RESOURCE_GROUP" ]] && ERRORS+=("IDENTITY_RESOURCE_GROUP (arg 2) is required")
[[ -z "$IDENTITY_NAME" ]]           && ERRORS+=("IDENTITY_NAME (arg 3) is required, e.g. k8s-servant-identity")
[[ -z "$CLUSTER_RESOURCE_GROUP" ]]  && ERRORS+=("CLUSTER_RESOURCE_GROUP (arg 4) is required")
[[ -z "$CLUSTER_NAME" ]]            && ERRORS+=("CLUSTER_NAME (arg 5) is required")
if [[ -z "$KEYVAULT_ID" ]]; then
  ERRORS+=("KEYVAULT_ID (arg 6) is required: az keyvault show --name <vault> --query id -o tsv")
elif [[ ! "$KEYVAULT_ID" =~ $KV_ID_PATTERN ]]; then
  ERRORS+=("KEYVAULT_ID (arg 6) must be a Key Vault resource ID (/subscriptions/.../providers/Microsoft.KeyVault/vaults/<name>)")
fi
if [[ -z "$FOUNDRY_ID" ]]; then
  ERRORS+=("FOUNDRY_ID (arg 7) is required: az cognitiveservices account show ... --query id -o tsv")
elif [[ ! "$FOUNDRY_ID" =~ $FOUNDRY_ID_PATTERN ]]; then
  ERRORS+=("FOUNDRY_ID (arg 7) must be a Foundry resource ID (/subscriptions/.../providers/Microsoft.CognitiveServices/accounts/<name>)")
fi

# The resource group of an ephemeral cluster is destroyed with it: an identity in it
# would block the destroy or be deleted with it (and its client ID would change).
if [[ "${EPHEMERAL_CLUSTER:-}" == "yes" && "$IDENTITY_RESOURCE_GROUP" == "$CLUSTER_RESOURCE_GROUP" ]]; then
  ERRORS+=("IDENTITY_RESOURCE_GROUP (arg 2) must not be $CLUSTER_RESOURCE_GROUP, which is recreated with the cluster (EPHEMERAL_CLUSTER=yes); use a long-lived resource group")
fi

if [[ ${#ERRORS[@]} -gt 0 ]]; then
  echo "ERROR: Invalid arguments:" >&2
  for err in "${ERRORS[@]}"; do echo "  - $err" >&2; done
  echo "" >&2
  echo "Usage: $0 <SUBSCRIPTION_NAME> <IDENTITY_RESOURCE_GROUP> <IDENTITY_NAME> <CLUSTER_RESOURCE_GROUP> <CLUSTER_NAME> <KEYVAULT_ID> <FOUNDRY_ID> [KEYVAULT_SECRET_NAME] [SERVICE_ACCOUNT_NAMESPACE] [SERVICE_ACCOUNT_NAME]" >&2
  exit 1
fi

if ! command -v az >/dev/null 2>&1; then
  echo "ERROR: the Azure CLI (az) is not installed or not on PATH." >&2
  exit 1
fi

# Parts of the Key Vault ID: `az keyvault show` / `set-policy` take name, group and subscription.
[[ "$KEYVAULT_ID" =~ $KV_ID_PATTERN ]]
KEYVAULT_SUBSCRIPTION="${BASH_REMATCH[1]}"
KEYVAULT_RESOURCE_GROUP="${BASH_REMATCH[2]}"
KEYVAULT_NAME="${BASH_REMATCH[3]}"

SUBJECT="system:serviceaccount:${SERVICE_ACCOUNT_NAMESPACE}:${SERVICE_ACCOUNT_NAME}"
# One credential per cluster, so one identity can serve several clusters.
FEDERATED_CREDENTIAL_NAME="${CLUSTER_NAME}-${IDENTITY_NAME}-fed-cred"

# Assign a role to the identity only if it does not have it yet. The principal
# type is given explicitly so a brand-new identity can be assigned at once,
# before Entra ID has replicated it.
assign_role() {
  local role="$1" scope="$2" count
  count=$(az role assignment list --assignee "$PRINCIPAL_ID" --role "$role" --scope "$scope" \
    --query "length(@)" -o tsv)
  if [[ "$count" == "0" ]]; then
    echo "    Assigning '$role' on $scope"
    az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
      --role "$role" --scope "$scope" --output none
  else
    echo "    '$role' on $scope already assigned"
  fi
}

# ---------------------------------------------------------------------------
# 1. Subscription, tenant, region, resource group
# ---------------------------------------------------------------------------
echo "==> Subscription: $SUBSCRIPTION_NAME"
az account set --subscription "$SUBSCRIPTION_NAME"
TENANT_ID=$(az account show --query tenantId -o tsv)
LOCATION=$(az group show --name "$CLUSTER_RESOURCE_GROUP" --query location -o tsv)

if az group show --name "$IDENTITY_RESOURCE_GROUP" --output none 2>/dev/null; then
  echo "==> Resource group $IDENTITY_RESOURCE_GROUP exists"
else
  echo "==> Creating resource group $IDENTITY_RESOURCE_GROUP in $LOCATION"
  az group create --name "$IDENTITY_RESOURCE_GROUP" --location "$LOCATION" --output none
fi

# ---------------------------------------------------------------------------
# 2. Managed identity
# ---------------------------------------------------------------------------
if az identity show --name "$IDENTITY_NAME" --resource-group "$IDENTITY_RESOURCE_GROUP" --output none 2>/dev/null; then
  echo "==> Identity $IDENTITY_NAME exists"
else
  echo "==> Creating identity $IDENTITY_NAME"
  az identity create --name "$IDENTITY_NAME" --resource-group "$IDENTITY_RESOURCE_GROUP" --location "$LOCATION" --output none
fi
CLIENT_ID=$(az identity show --name "$IDENTITY_NAME" --resource-group "$IDENTITY_RESOURCE_GROUP" --query clientId -o tsv)
PRINCIPAL_ID=$(az identity show --name "$IDENTITY_NAME" --resource-group "$IDENTITY_RESOURCE_GROUP" --query principalId -o tsv)

# ---------------------------------------------------------------------------
# 3. Federated credential (create, or fix the issuer after a cluster recreation)
# ---------------------------------------------------------------------------
OIDC_ISSUER=$(az aks show --resource-group "$CLUSTER_RESOURCE_GROUP" --name "$CLUSTER_NAME" \
  --query oidcIssuerProfile.issuerUrl -o tsv)
if [[ -z "$OIDC_ISSUER" ]]; then
  echo "ERROR: cluster $CLUSTER_NAME has no OIDC issuer. Enable it with:" >&2
  echo "  az aks update --resource-group $CLUSTER_RESOURCE_GROUP --name $CLUSTER_NAME --enable-oidc-issuer --enable-workload-identity" >&2
  exit 1
fi

STORED_ISSUER=$(az identity federated-credential show --name "$FEDERATED_CREDENTIAL_NAME" \
  --identity-name "$IDENTITY_NAME" --resource-group "$IDENTITY_RESOURCE_GROUP" --query issuer -o tsv 2>/dev/null || true)
if [[ -z "$STORED_ISSUER" ]]; then
  echo "==> Creating federated credential for $SUBJECT"
  az identity federated-credential create --name "$FEDERATED_CREDENTIAL_NAME" \
    --identity-name "$IDENTITY_NAME" --resource-group "$IDENTITY_RESOURCE_GROUP" \
    --issuer "$OIDC_ISSUER" --subject "$SUBJECT" --output none
elif [[ "$STORED_ISSUER" != "$OIDC_ISSUER" ]]; then
  echo "==> Federated credential points to an old issuer (cluster recreated?); updating it"
  az identity federated-credential update --name "$FEDERATED_CREDENTIAL_NAME" \
    --identity-name "$IDENTITY_NAME" --resource-group "$IDENTITY_RESOURCE_GROUP" \
    --issuer "$OIDC_ISSUER" --subject "$SUBJECT" --output none
else
  echo "==> Federated credential for $SUBJECT is up to date"
fi

# ---------------------------------------------------------------------------
# 4. Key Vault: read the one secret
# ---------------------------------------------------------------------------
# Stop if the vault cannot be read: guessing "access policy" would grant every secret.
if ! KV_RBAC=$(az keyvault show --name "$KEYVAULT_NAME" --resource-group "$KEYVAULT_RESOURCE_GROUP" \
  --subscription "$KEYVAULT_SUBSCRIPTION" --query properties.enableRbacAuthorization -o tsv); then
  echo "ERROR: cannot read Key Vault $KEYVAULT_NAME (wrong ID, or no access). Nothing was granted on it." >&2
  exit 1
fi
if [[ "$KV_RBAC" == "true" ]]; then
  echo "==> Key Vault $KEYVAULT_NAME uses Azure RBAC: granting access to secret $KEYVAULT_SECRET_NAME only"
  KV_ACCESS="RBAC, scoped to secret $KEYVAULT_SECRET_NAME"
  assign_role "$KV_ROLE" "${KEYVAULT_ID}/secrets/${KEYVAULT_SECRET_NAME}"
else
  # An access policy cannot be limited to one secret. Granting it would let the agent's
  # identity read every secret in a (usually shared) vault, so it needs an explicit yes.
  if [[ "${ALLOW_VAULT_WIDE_SECRET_READ:-}" != "yes" ]]; then
    echo "ERROR: Key Vault $KEYVAULT_NAME uses access policies, which cannot be limited to one secret." >&2
    echo "  Granting 'get' would let the identity read EVERY secret in $KEYVAULT_NAME. Either:" >&2
    echo "  - switch the vault to Azure RBAC (then only $KEYVAULT_SECRET_NAME is granted), or" >&2
    echo "  - copy the secret into a vault of its own, or" >&2
    echo "  - accept it: re-run with ALLOW_VAULT_WIDE_SECRET_READ=yes" >&2
    exit 1
  fi
  echo "==> Key Vault $KEYVAULT_NAME uses access policies: setting 'get' on secrets (ALLOW_VAULT_WIDE_SECRET_READ=yes)"
  echo "    WARNING: the identity can read EVERY secret in $KEYVAULT_NAME."
  KV_ACCESS="access policy (get on all secrets)"
  az keyvault set-policy --name "$KEYVAULT_NAME" --resource-group "$KEYVAULT_RESOURCE_GROUP" \
    --subscription "$KEYVAULT_SUBSCRIPTION" --object-id "$PRINCIPAL_ID" --secret-permissions get --output none
fi

# ---------------------------------------------------------------------------
# 5. Foundry: call the model
# ---------------------------------------------------------------------------
echo "==> Foundry: '$FOUNDRY_ROLE'"
assign_role "$FOUNDRY_ROLE" "$FOUNDRY_ID"

# ---------------------------------------------------------------------------
# 6. Summary
# ---------------------------------------------------------------------------
echo ""
echo "======================================================================"
echo " Workload Identity ready"
echo "   Identity:   $IDENTITY_NAME ($IDENTITY_RESOURCE_GROUP)"
echo "   Subject:    $SUBJECT"
echo "   Issuer:     $OIDC_ISSUER"
echo "   Key Vault:  $KEYVAULT_NAME ($KV_ACCESS)"
echo "   Foundry:    ${FOUNDRY_ID##*/}"
echo "======================================================================"
echo ""
echo "1. Put this into the Helm values of the k8s-servant release on $CLUSTER_NAME:"
echo "----------------------------------------------------------------------"
echo "  azureWorkloadIdentity:"
echo "    clientId: \"${CLIENT_ID}\""
echo "    tenantId: \"${TENANT_ID}\""
echo ""
echo "2. The pod's service account must be ${SERVICE_ACCOUNT_NAMESPACE}/${SERVICE_ACCOUNT_NAME}"
echo "   (the subject of federated credential ${FEDERATED_CREDENTIAL_NAME})."
if [[ "${EPHEMERAL_CLUSTER:-}" == "yes" ]]; then
  echo ""
  echo "3. $CLUSTER_NAME is ephemeral (EPHEMERAL_CLUSTER=yes): every recreation gives it a new"
  echo "   OIDC issuer. Re-run this script after each recreation (or have your recreation"
  echo "   automation update federated credential ${FEDERATED_CREDENTIAL_NAME})."
fi
echo "======================================================================"
echo "Role assignments can take a few minutes to take effect."
