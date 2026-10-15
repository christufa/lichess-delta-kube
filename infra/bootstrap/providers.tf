# This file specifies how to configure each provider.
# The azurerm provider requires features {} to be specified, even if no features are enabled,
# and the subscription_id must be set.
provider "azurerm" {
  # https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs
  features {
    storage {
      # Manage storage only through Azure Resource Manager, never the blob endpoint,
      # so the firewall can deny everything without breaking Terraform.
      data_plane_available = false
    }
  }
  subscription_id     = var.subscription_id
  storage_use_azuread = true # makes Terraform use your Entra ID sign-in for storage, which 
  # is needed because account keys will be turned off.
}