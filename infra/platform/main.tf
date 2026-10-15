# The identity running Terraform (you, via az login). Used for the role assignment.
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/client_config
data "azurerm_client_config" "current" {}

locals {
  tags = {
    project    = var.project
    component  = "platform"
    managed_by = "terraform"
  }
}

# Storage account names are global across Azure, so they get a random suffix.
resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/resource_group
resource "azurerm_resource_group" "platform" {
  name     = "rg-${var.project}-platform"
  location = var.location
  tags     = local.tags
}
