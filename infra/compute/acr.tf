# Private image registry for the ingest image (and Spark images later).
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/container_registry
resource "azurerm_container_registry" "main" {
  name                = "acr${var.project}${random_string.suffix.result}" # letters and digits only
  resource_group_name = azurerm_resource_group.compute.name
  location            = azurerm_resource_group.compute.location
  sku                 = "Basic" # ~$5/month, 10 GB included
  admin_enabled       = false   # Entra ID only: az acr login
  tags                = local.tags
}

# Lets you `docker push` from your PC after `az acr login`.
resource "azurerm_role_assignment" "acr_me" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPush"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Lets the cluster's nodes pull images.
resource "azurerm_role_assignment" "acr_aks" {
  scope                            = azurerm_container_registry.main.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
  skip_service_principal_aad_check = true
}
