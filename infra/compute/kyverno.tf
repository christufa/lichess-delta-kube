# --- Kyverno admission controller: read the registry to verify signatures ----------
# The image policy (deploy/charts/platform) checks cosign signatures and resolves tags to
# digests, so Kyverno must read manifests and signatures from ACR: AcrPull via workload
# identity (credentials provider "azure" in the policy). No registry secrets.
resource "azurerm_user_assigned_identity" "kyverno" {
  name                = "id-${var.project}-kyverno"
  resource_group_name = azurerm_resource_group.compute.name
  location            = azurerm_resource_group.compute.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "kyverno_acr" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.kyverno.principal_id
}

resource "azurerm_federated_identity_credential" "kyverno" {
  name                      = "aks-kyverno-admission-controller"
  user_assigned_identity_id = azurerm_user_assigned_identity.kyverno.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject                   = "system:serviceaccount:kyverno:kyverno-admission-controller"
}
