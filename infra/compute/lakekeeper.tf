# --- Lakekeeper (Iceberg REST catalog, #9): its own identity for the lake ---------------
# Lakekeeper reads and writes table metadata and vends short-lived SAS tokens, scoped to one
# table's location, to Spark/Trino. User-delegation SAS needs Storage Blob Delegator next to
# the data role. It signs in with workload identity (LAKEKEEPER__ENABLE_AZURE_SYSTEM_CREDENTIALS).
resource "azurerm_user_assigned_identity" "lakekeeper" {
  name                = "id-${var.project}-lakekeeper"
  resource_group_name = azurerm_resource_group.compute.name
  location            = azurerm_resource_group.compute.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "lakekeeper_lake" {
  for_each             = toset(["Storage Blob Data Contributor", "Storage Blob Delegator"])
  scope                = local.platform.lake_account_id
  role_definition_name = each.value
  principal_id         = azurerm_user_assigned_identity.lakekeeper.principal_id
}

resource "azurerm_federated_identity_credential" "lakekeeper" {
  name                      = "aks-lakekeeper-lakekeeper"
  user_assigned_identity_id = azurerm_user_assigned_identity.lakekeeper.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject                   = "system:serviceaccount:lakekeeper:lakekeeper"
}
