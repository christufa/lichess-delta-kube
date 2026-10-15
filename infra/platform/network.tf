# Shared network. Lives here (not in compute) because the lake's firewall trusts
# the AKS subnet, and compute reads these IDs from this stack's outputs.
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/virtual_network
resource "azurerm_virtual_network" "main" {
  name                = "vnet-${var.project}"
  resource_group_name = azurerm_resource_group.platform.name
  location            = azurerm_resource_group.platform.location
  address_space       = ["10.10.0.0/16"]
  tags                = local.tags
}

# AKS nodes. With Azure CNI Overlay pods get IPs from a separate range, so a /22
# (~1000 nodes) is plenty. The Microsoft.Storage service endpoint lets nodes
# reach the lake through its firewall without public IP rules.
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/subnet
resource "azurerm_subnet" "aks" {
  name                 = "snet-aks"
  resource_group_name  = azurerm_resource_group.platform.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.10.0.0/22"]
  service_endpoints    = ["Microsoft.Storage"]
}
