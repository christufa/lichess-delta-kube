# --- Chat (#69): the identity the chat backend calls the MCP server with -------------------
# chess-chat signs in as this managed identity (workload identity: its service account's token is
# federated with it; no secret). infra/gitops assigns it the MCP app's role Lakehouse.Query, so it
# can query the lakehouse through the MCP server and nothing else in Azure.
resource "azurerm_user_assigned_identity" "chat" {
  name                = "id-${var.project}-chat"
  resource_group_name = azurerm_resource_group.compute.name
  location            = azurerm_resource_group.compute.location
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "chat" {
  name                      = "aks-chess-chat-chess-chat"
  user_assigned_identity_id = azurerm_user_assigned_identity.chat.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject                   = "system:serviceaccount:chess-chat:chess-chat"
}
