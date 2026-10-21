#!/usr/bin/env bash
# Render and validate everything Argo CD syncs from this repo:
#   - deploy/charts/*: helm lint + helm template with placeholder cluster facts
#   - deploy/apps, deploy/lichess, deploy/network-policies, rendered charts: kubeconform (strict) against Kubernetes
#     and CRD schemas (datreeio CRDs-catalog; kinds without a schema are reported, not failed)
set -euo pipefail
out=$(mktemp -d)

for chart in deploy/charts/*/; do
  name=$(basename "$chart")
  helm lint "$chart" \
    --set keyVaultUrl=https://kv-example.vault.azure.net/ \
    --set tenantId=00000000-0000-0000-0000-000000000000 \
    --set entra.tenantId=00000000-0000-0000-0000-000000000000 --set entra.clientId=00000000-0000-0000-0000-000000000001 \
    --set acrLoginServer=acrexample.azurecr.io
  helm template "$name" "$chart" \
    --set keyVaultUrl=https://kv-example.vault.azure.net/ \
    --set tenantId=00000000-0000-0000-0000-000000000000 \
    --set entra.tenantId=00000000-0000-0000-0000-000000000000 --set entra.clientId=00000000-0000-0000-0000-000000000001 \
    --set acrLoginServer=acrexample.azurecr.io > "$out/$name.yaml"
  echo "rendered $chart"
done

# chess-api / chess-web / chess-mcp as Argo CD renders them once deploy-state holds a build (images.yml,
# job release): the image is pinned by digest.
digest=sha256:$(printf '%064d' 0)
for name in chess-api chess-web chess-mcp; do
  helm template "$name" "deploy/charts/$name" \
    --set acrLoginServer=acrexample.azurecr.io \
    --set entra.tenantId=00000000-0000-0000-0000-000000000000 --set entra.clientId=00000000-0000-0000-0000-000000000001 \
    --set image.digest="$digest" \
    --set image.commit=0123456789abcdef0123456789abcdef01234567 > "$out/$name-pinned.yaml"
  grep -q "image: \"acrexample.azurecr.io/lichess-${name#chess-}@$digest\"" "$out/$name-pinned.yaml" \
    || { echo "$name: image not pinned to the digest"; exit 1; }
  echo "rendered $name pinned to a digest"
done

kubeconform -strict -summary -ignore-missing-schemas \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  deploy/apps deploy/lichess deploy/examples deploy/network-policies "$out"
