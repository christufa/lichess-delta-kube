#!/usr/bin/env bash
# Sign in to Azure with this job's GitHub OIDC token (federated credential on id-lichess-ci).
# Reports the token's subject and Azure's error as annotations, so a mismatch is obvious.
set -euo pipefail
: "${AZURE_CLIENT_ID:?secret AZURE_CLIENT_ID is not set}"
: "${AZURE_TENANT_ID:?secret AZURE_TENANT_ID is not set}"

token=$(curl -sSf -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
  "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=api://AzureADTokenExchange" | jq -r .value)
echo "::add-mask::$token"

# The subject must equal the federated credential's subject exactly (infra/compute/ci.tf).
sub=$(python3 -c 'import sys,json,base64;p=sys.argv[1].split(".")[1];p+="="*(-len(p)%4);print(json.loads(base64.urlsafe_b64decode(p))["sub"])' "$token")
echo "::notice title=GitHub OIDC subject::$sub"

if ! az login --service-principal -u "$AZURE_CLIENT_ID" --tenant "$AZURE_TENANT_ID" \
     --federated-token "$token" --allow-no-subscriptions --output none 2> az-login.err; then
  grep -v '^\s*$' az-login.err | head -6 | while IFS= read -r line; do echo "::error title=az login::$line"; done
  exit 1
fi
if [ -n "${AZURE_SUBSCRIPTION_ID:-}" ]; then
  az account set --subscription "$AZURE_SUBSCRIPTION_ID" 2>/dev/null \
    || echo "::warning title=az login::subscription not visible to the CI identity (only matters for name lookups)"
fi
echo "signed in to Azure as the CI identity"
