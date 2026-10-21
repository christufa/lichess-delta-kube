output "resource_group_name" {
  value = azurerm_resource_group.compute.name
}

output "aks_name" {
  value = azurerm_kubernetes_cluster.main.name
}

output "acr_login_server" {
  value = azurerm_container_registry.main.login_server
}

# Annotate the jobs service account with this:
#   azure.workload.identity/client-id: <value>
output "jobs_identity_client_id" {
  value = azurerm_user_assigned_identity.jobs.client_id
}

output "jobs_namespace" {
  value = var.jobs_namespace
}

output "jobs_service_account" {
  value = var.jobs_service_account
}

output "get_credentials" {
  value = "az aks get-credentials -g ${azurerm_resource_group.compute.name} -n ${azurerm_kubernetes_cluster.main.name} && kubelogin convert-kubeconfig -l azurecli"
}

# GitHub repository secrets for .github/workflows/images.yml (IDs, not credentials):
#   AZURE_CLIENT_ID = ci_client_id, ACR_NAME = acr_name,
#   AZURE_TENANT_ID / AZURE_SUBSCRIPTION_ID from `az account show`
output "ci_client_id" {
  value = azurerm_user_assigned_identity.ci.client_id
}

output "acr_name" {
  value = azurerm_container_registry.main.name
}

output "key_vault_name" {
  value = azurerm_key_vault.main.name
}

output "key_vault_uri" {
  value = azurerm_key_vault.main.vault_uri
}

output "eso_identity_client_id" {
  value = azurerm_user_assigned_identity.eso.client_id
}

output "eso_namespace" {
  value = var.eso_namespace
}

output "eso_service_account" {
  value = var.eso_service_account
}

output "kyverno_identity_client_id" {
  value = azurerm_user_assigned_identity.kyverno.client_id
}

output "prometheus_query_endpoint" {
  value = azurerm_monitor_workspace.main.query_endpoint
}

output "grafana_identity_client_id" {
  value = azurerm_user_assigned_identity.grafana.client_id
}

output "lakekeeper_identity_client_id" {
  value = azurerm_user_assigned_identity.lakekeeper.client_id
}

output "trino_identity_client_id" {
  value = azurerm_user_assigned_identity.trino.client_id
}

output "cost_resource_groups" {
  description = "Resource groups whose costs go into lake.gold.azure_cost (#31)"
  value       = local.cost_resource_groups
}

output "chat_identity_client_id" {
  description = "Managed identity of the chat backend (workload identity for chess-chat, #69)"
  value       = azurerm_user_assigned_identity.chat.client_id
}

output "chat_identity_principal_id" {
  description = "Its service principal: infra/gitops assigns it the MCP app's role Lakehouse.Query"
  value       = azurerm_user_assigned_identity.chat.principal_id
}
