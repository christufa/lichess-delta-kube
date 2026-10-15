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
