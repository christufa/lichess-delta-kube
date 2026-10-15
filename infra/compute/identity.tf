# --- Cluster (control plane) identity ---------------------------------------
# User-assigned so it can be given rights on the platform subnet *before* the
# cluster is created (a system-assigned one only exists after).
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/user_assigned_identity
resource "azurerm_user_assigned_identity" "aks" {
  name                = "id-${var.project}-aks"
  resource_group_name = azurerm_resource_group.compute.name
  location            = azurerm_resource_group.compute.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "aks_network" {
  scope                = local.platform.aks_subnet_id
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_user_assigned_identity.aks.principal_id
}

# --- Jobs identity (workload identity) --------------------------------------
# Pods running as the `jobs_service_account` service account get Azure tokens for
# this identity, which can read and write the lake. No secrets or SAS tokens.
# https://learn.microsoft.com/azure/aks/workload-identity-overview
resource "azurerm_user_assigned_identity" "jobs" {
  name                = "id-${var.project}-jobs"
  resource_group_name = azurerm_resource_group.compute.name
  location            = azurerm_resource_group.compute.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "jobs_lake" {
  scope                = local.platform.lake_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.jobs.principal_id
}

# Trust tokens that this cluster issues for exactly one service account.
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/federated_identity_credential
resource "azurerm_federated_identity_credential" "jobs" {
  name                = "aks-${var.jobs_namespace}-${var.jobs_service_account}"
  resource_group_name = azurerm_resource_group.compute.name
  parent_id           = azurerm_user_assigned_identity.jobs.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject             = "system:serviceaccount:${var.jobs_namespace}:${var.jobs_service_account}"
}

# --- You ---------------------------------------------------------------------
# Local (certificate) accounts are disabled, so kubectl access is through Entra ID.
resource "azurerm_role_assignment" "aks_me" {
  scope                = azurerm_kubernetes_cluster.main.id
  role_definition_name = "Azure Kubernetes Service RBAC Cluster Admin"
  principal_id         = data.azurerm_client_config.current.object_id
}
