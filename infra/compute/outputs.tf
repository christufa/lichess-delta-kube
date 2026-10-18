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
