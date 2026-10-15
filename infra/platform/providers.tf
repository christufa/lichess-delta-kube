provider "azurerm" {
  features {
    storage {
      # Manage storage only through Azure Resource Manager, never the blob endpoint,
      # so the firewall can deny everything without breaking Terraform.
      data_plane_available = false
    }
  }
  subscription_id     = var.subscription_id
  storage_use_azuread = true # no shared keys anywhere; Entra ID only
}
