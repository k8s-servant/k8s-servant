#!/bin/bash
# =============================================================================
# setup-foundry.sh
# Creates the MS Foundry resource and the chat model deployment that the
# k8s-servant calls, and grants the signed-in user (you) the data-plane role
# needed to call the model for local testing.
#
# What it does (each step is skipped if it already exists, so it is safe to
# re-run):
#   1. Ensures the resource group exists (in the given region).
#   2. Creates the Foundry resource (kind AIServices, SKU S0, custom subdomain =
#      resource name, system-assigned identity).
#   3. Creates the model deployment (default: gpt-5.4-mini 2026-03-17,
#      DataZoneStandard = processed inside the EU data zone, 50k tokens/minute)
#      and sets its version upgrade policy (default: OnceCurrentVersionExpired =
#      keep the tested version until its retirement date). The policy is also
#      corrected on an existing deployment.
#   4. Assigns "Cognitive Services OpenAI User" on the resource to the signed-in
#      user, so `k8s-servant doctor` / `ask` work from your laptop with `az login`.
#   5. Prints the LLM_PROVIDER / LLM_ENDPOINT / LLM_MODEL values for .env.
#
# What it does NOT do (see docs/foundry-setup.md):
#   - Networking (private endpoint / firewall) for the private AKS clusters.
#   - The agent's managed identity: scripts/setup-workload-identity.sh.
#   - A budget alert: `az consumption budget create` cannot add e-mail
#     notifications, so the script prints the portal steps instead.
#
# Cost: the resource and an idle deployment are free; you pay per token used.
#
# Layout suggestion (docs/foundry-concepts.md): one resource for non-prod
# clusters and one for prod, each in the subscription and region you choose.
#
# Usage:
#   ./scripts/setup-foundry.sh \
#     <SUBSCRIPTION_NAME> <RESOURCE_GROUP> <FOUNDRY_NAME> <LOCATION> \
#     [DEPLOYMENT_NAME] [MODEL_NAME] [MODEL_VERSION] [SKU_NAME] [CAPACITY] [UPGRADE_OPTION]
#
# UPGRADE_OPTION: OnceCurrentVersionExpired (default) | OnceNewDefaultVersionAvailable
#   (the portal default: the model changes within two weeks of a new default
#   version) | NoAutoUpgrade (the deployment stops working at retirement).
#
# Example:
#   ./scripts/setup-foundry.sh \
#     "My Subscription" "my-foundry-rg" "my-foundry" "westeurope"
#
# Required permissions: Contributor on the resource group (or subscription, to
# create the group) and Owner / User Access Administrator for the role
# assignment.
# =============================================================================
set -euo pipefail

SUBSCRIPTION_NAME=${1:-""}
RESOURCE_GROUP=${2:-""}
FOUNDRY_NAME=${3:-""}
LOCATION=${4:-""}
DEPLOYMENT_NAME=${5:-"k8s-servant"}
MODEL_NAME=${6:-"gpt-5.4-mini"}
MODEL_VERSION=${7:-"2026-03-17"}
SKU_NAME=${8:-"DataZoneStandard"}
CAPACITY=${9:-"50"}
UPGRADE_OPTION=${10:-"OnceCurrentVersionExpired"}

ROLE="Cognitive Services OpenAI User"

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
ERRORS=()

[[ -z "$SUBSCRIPTION_NAME" ]] && ERRORS+=("SUBSCRIPTION_NAME (arg 1) is required")
[[ -z "$RESOURCE_GROUP" ]]    && ERRORS+=("RESOURCE_GROUP (arg 2) is required")
[[ -z "$FOUNDRY_NAME" ]]      && ERRORS+=("FOUNDRY_NAME (arg 3) is required")
[[ -z "$LOCATION" ]]          && ERRORS+=("LOCATION (arg 4) is required, e.g. swedencentral or westeurope")
[[ "$CAPACITY" =~ ^[0-9]+$ ]] || ERRORS+=("CAPACITY (arg 9) must be a whole number (units of 1,000 tokens/minute)")
case "$SKU_NAME" in
  GlobalStandard|DataZoneStandard|Standard) ;;
  *) ERRORS+=("SKU_NAME (arg 8) must be GlobalStandard, DataZoneStandard or Standard") ;;
esac
case "$UPGRADE_OPTION" in
  OnceCurrentVersionExpired|OnceNewDefaultVersionAvailable|NoAutoUpgrade) ;;
  *) ERRORS+=("UPGRADE_OPTION (arg 10) must be OnceCurrentVersionExpired, OnceNewDefaultVersionAvailable or NoAutoUpgrade") ;;
esac

if [[ ${#ERRORS[@]} -gt 0 ]]; then
  echo "ERROR: Invalid parameters:" >&2
  for err in "${ERRORS[@]}"; do
    echo "  - $err" >&2
  done
  echo "" >&2
  echo "Usage: $0 <SUBSCRIPTION_NAME> <RESOURCE_GROUP> <FOUNDRY_NAME> <LOCATION> \\" >&2
  echo "          [DEPLOYMENT_NAME] [MODEL_NAME] [MODEL_VERSION] [SKU_NAME] [CAPACITY] [UPGRADE_OPTION]" >&2
  exit 1
fi

if ! command -v az &>/dev/null; then
  echo "ERROR: Azure CLI (az) is not installed or not in PATH." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Subscription context and signed-in user
# ---------------------------------------------------------------------------
echo "==> Setting subscription context: $SUBSCRIPTION_NAME"
az account set --subscription "$SUBSCRIPTION_NAME"

USER_OBJECT_ID=$(az ad signed-in-user show --query id -o tsv)
USER_NAME=$(az ad signed-in-user show --query userPrincipalName -o tsv)
echo "    Signed-in user: $USER_NAME"

# ---------------------------------------------------------------------------
# 1. Resource group
# ---------------------------------------------------------------------------
echo "==> Ensuring resource group '$RESOURCE_GROUP' exists..."
if az group show --name "$RESOURCE_GROUP" &>/dev/null; then
  echo "    Resource group already exists."
else
  echo "    Not found - creating in '$LOCATION'..."
  az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
fi

# ---------------------------------------------------------------------------
# 2. Foundry resource
# ---------------------------------------------------------------------------
echo "==> Ensuring Foundry resource '$FOUNDRY_NAME' exists..."
if az cognitiveservices account show --name "$FOUNDRY_NAME" --resource-group "$RESOURCE_GROUP" &>/dev/null; then
  echo "    Foundry resource already exists."
else
  echo "    Not found - creating (kind AIServices, SKU S0, custom subdomain '$FOUNDRY_NAME')..."
  # --custom-domain: needed for Entra ID auth and private endpoints; must be
  #   globally unique (error CustomDomainInUse otherwise - pick another name).
  # --assign-identity: system identity, needed if a Foundry project is ever added.
  # --yes: accept the responsible-AI terms non-interactively.
  az cognitiveservices account create \
    --name "$FOUNDRY_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --kind AIServices \
    --sku S0 \
    --custom-domain "$FOUNDRY_NAME" \
    --assign-identity \
    --yes \
    --output none
fi

FOUNDRY_ID=$(az cognitiveservices account show \
  --name "$FOUNDRY_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)

# ---------------------------------------------------------------------------
# 3. Model deployment
# ---------------------------------------------------------------------------
echo "==> Ensuring deployment '$DEPLOYMENT_NAME' ($MODEL_NAME $MODEL_VERSION, $SKU_NAME, capacity $CAPACITY) exists..."
if az cognitiveservices account deployment show --name "$FOUNDRY_NAME" --resource-group "$RESOURCE_GROUP" \
     --deployment-name "$DEPLOYMENT_NAME" &>/dev/null; then
  echo "    Deployment already exists (not changed; edit it in the Foundry portal or delete and re-run)."
else
  echo "    Not found - creating..."
  if ! az cognitiveservices account deployment create \
      --name "$FOUNDRY_NAME" \
      --resource-group "$RESOURCE_GROUP" \
      --deployment-name "$DEPLOYMENT_NAME" \
      --model-name "$MODEL_NAME" \
      --model-version "$MODEL_VERSION" \
      --model-format OpenAI \
      --sku-name "$SKU_NAME" \
      --sku-capacity "$CAPACITY" \
      --output none; then
    echo "" >&2
    echo "ERROR: deployment failed. Common causes:" >&2
    echo "  - Model/version/type not offered in '$LOCATION'. List what is available:" >&2
    echo "      az cognitiveservices account list-models -n $FOUNDRY_NAME -g $RESOURCE_GROUP -o table" >&2
    echo "  - Not enough quota for $MODEL_NAME ($SKU_NAME) in this subscription/region:" >&2
    echo "      lower CAPACITY (arg 9) or request quota in the Foundry portal (Quota)." >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# 3b. Version upgrade policy of the deployment
# ---------------------------------------------------------------------------
# `az cognitiveservices account deployment create/update` cannot set this
# property (documented limitation), so it is set on the ARM resource directly.
# Empty (null) means OnceCurrentVersionExpired to Azure, but the portal shows
# nothing then, so the script always sets it explicitly.
echo "==> Ensuring version upgrade policy $UPGRADE_OPTION on '$DEPLOYMENT_NAME'..."
CURRENT_UPGRADE_OPTION=$(az cognitiveservices account deployment show --name "$FOUNDRY_NAME" \
  --resource-group "$RESOURCE_GROUP" --deployment-name "$DEPLOYMENT_NAME" \
  --query properties.versionUpgradeOption -o tsv)
if [[ "$CURRENT_UPGRADE_OPTION" == "$UPGRADE_OPTION" ]]; then
  echo "    Already set."
else
  echo "    Changing from '${CURRENT_UPGRADE_OPTION:-not set}'..."
  if ! az resource update \
      --ids "$FOUNDRY_ID/deployments/$DEPLOYMENT_NAME" \
      --set properties.versionUpgradeOption="$UPGRADE_OPTION" \
      --api-version 2025-06-01 \
      --output none; then
    # Not fatal: the deployment works either way. Fix it by hand.
    echo "    WARNING: could not set it. In the Foundry portal: Deployments -> $DEPLOYMENT_NAME" >&2
    echo "             -> Edit -> Version update policy." >&2
  fi
fi

# ---------------------------------------------------------------------------
# 4. Data-plane role for the signed-in user (local testing)
# ---------------------------------------------------------------------------
# Owner/Contributor manage the resource but cannot call models; this role can.
echo "==> Assigning '$ROLE' on the Foundry resource to $USER_NAME..."
EXISTING=$(az role assignment list --assignee "$USER_OBJECT_ID" --role "$ROLE" --scope "$FOUNDRY_ID" --query "length(@)" -o tsv)
if [[ "$EXISTING" != "0" ]]; then
  echo "    Role already assigned."
else
  az role assignment create \
    --assignee-object-id "$USER_OBJECT_ID" \
    --assignee-principal-type User \
    --role "$ROLE" \
    --scope "$FOUNDRY_ID" \
    --output none
  echo "    Role assigned. It can take ~5 minutes before model calls are allowed (HTTP 401 until then)."
fi

# ---------------------------------------------------------------------------
# Output summary
# ---------------------------------------------------------------------------
echo ""
echo "======================================================================"
echo "MS Foundry setup complete"
echo "======================================================================"
echo "Resource:    $FOUNDRY_NAME ($RESOURCE_GROUP, $LOCATION)"
echo "Deployment:  $DEPLOYMENT_NAME -> $MODEL_NAME $MODEL_VERSION ($SKU_NAME, ${CAPACITY}k tokens/minute,"
echo "             upgrade policy $UPGRADE_OPTION)"
echo ""
echo "1. Put these into your .env (then: set -a; source .env; set +a):"
echo "----------------------------------------------------------------------"
echo "  LLM_PROVIDER=foundry"
echo "  LLM_ENDPOINT=https://${FOUNDRY_NAME}.openai.azure.com/"
echo "  LLM_MODEL=${DEPLOYMENT_NAME}"
echo "  LLM_AUTH=entra"
echo ""
echo "2. Check it end to end:"
echo "----------------------------------------------------------------------"
echo "  k8s-servant doctor"
echo ""
echo "3. Recommended - budget alert (free; the CLI cannot add e-mail alerts):"
echo "----------------------------------------------------------------------"
echo "  Azure portal -> Cost Management -> Budgets -> + Add"
echo "  Scope: resource group '$RESOURCE_GROUP', monthly amount, alert at 80% to your e-mail."
echo ""
echo "Next for the cluster: private endpoint or firewall rule"
echo "(docs/foundry-setup.md, Step 5) and scripts/setup-workload-identity.sh."
echo "======================================================================"
