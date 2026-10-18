# --- Key Vault: the only place secret values live ------------------------------
# Kubernetes gets them through External Secrets Operator (deploy/apps/external-secrets.yaml),
# which signs in with workload identity. Terraform never reads or writes secret values.
#
# Firewall: deny by default; the AKS subnet is allowed (service endpoint). To set secrets
# from your PC, add your IP by hand in the portal (Networking → Firewall), like the lake:
# Terraform ignores ip_rules.
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/key_vault
resource "azurerm_key_vault" "main" {
  name                       = "kv-${var.project}-${random_string.suffix.result}"
  resource_group_name        = azurerm_resource_group.compute.name
  location                   = azurerm_resource_group.compute.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true  # Azure RBAC for secrets, no access policies
  soft_delete_retention_days = 7     # the minimum
  purge_protection_enabled   = false # teardown (#48) must be able to purge it

  public_network_access_enabled = true # reachable only as the firewall below allows
  network_acls {
    default_action             = "Deny"
    bypass                     = "None"
    virtual_network_subnet_ids = [local.platform.aks_subnet_id]
    ip_rules                   = []
  }

  tags = local.tags

  lifecycle {
    ignore_changes = [network_acls[0].ip_rules] # your IP is set by hand in Azure
  }
}

# You: create and update secret values (portal, or az keyvault secret set).
resource "azurerm_role_assignment" "kv_me" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# External Secrets Operator: read secret values, nothing else.
resource "azurerm_user_assigned_identity" "eso" {
  name                = "id-${var.project}-eso"
  resource_group_name = azurerm_resource_group.compute.name
  location            = azurerm_resource_group.compute.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "kv_eso" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.eso.principal_id
}

resource "azurerm_federated_identity_credential" "eso" {
  name                      = "aks-${var.eso_namespace}-${var.eso_service_account}"
  user_assigned_identity_id = azurerm_user_assigned_identity.eso.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject                   = "system:serviceaccount:${var.eso_namespace}:${var.eso_service_account}"
}
