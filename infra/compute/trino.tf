# --- Trino (#64): read-only access to the lake ------------------------------------------
# Trino only reads (its catalog is read_only and Lakekeeper grants it select), so its own
# identity gets Storage Blob Data Reader: even a bug can't write to the lake.
resource "azurerm_user_assigned_identity" "trino" {
  name                = "id-${var.project}-trino"
  resource_group_name = azurerm_resource_group.compute.name
  location            = azurerm_resource_group.compute.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "trino_lake" {
  scope                = local.platform.lake_account_id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_user_assigned_identity.trino.principal_id
}

resource "azurerm_federated_identity_credential" "trino" {
  name                      = "aks-trino-trino"
  user_assigned_identity_id = azurerm_user_assigned_identity.trino.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject                   = "system:serviceaccount:trino:trino"
}
