#!/usr/bin/env bash
# Render and validate everything Argo CD syncs from this repo:
#   - deploy/charts/*: helm lint + helm template with placeholder cluster facts
#   - deploy/apps, deploy/lichess, rendered charts: kubeconform (strict) against Kubernetes
#     and CRD schemas (datreeio CRDs-catalog; kinds without a schema are reported, not failed)
set -euo pipefail
out=$(mktemp -d)

for chart in deploy/charts/*/; do
  name=$(basename "$chart")
  helm lint "$chart" \
    --set keyVaultUrl=https://kv-example.vault.azure.net/ \
    --set tenantId=00000000-0000-0000-0000-000000000000 \
    --set acrLoginServer=acrexample.azurecr.io
  helm template "$name" "$chart" \
    --set keyVaultUrl=https://kv-example.vault.azure.net/ \
    --set tenantId=00000000-0000-0000-0000-000000000000 \
    --set acrLoginServer=acrexample.azurecr.io > "$out/$name.yaml"
  echo "rendered $chart"
done

kubeconform -strict -summary -ignore-missing-schemas \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  deploy/apps deploy/lichess deploy/examples "$out"
