# The data lake: ADLS Gen2 (hierarchical namespace) holding landing, raw and curated data.
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_account
resource "azurerm_storage_account" "lake" {
  name                     = "st${var.project}lake${random_string.suffix.result}" # <= 24 chars
  resource_group_name      = azurerm_resource_group.platform.name
  location                 = azurerm_resource_group.platform.location
  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "LRS" # the source is public; it can always be re-ingested
  access_tier              = "Hot"

  # ADLS Gen2. Can only be set at creation: changing it later destroys the account.
  is_hns_enabled = true

  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  shared_access_key_enabled       = false # Entra ID (and user-delegation SAS) only
  allow_nested_items_to_be_public = false

  # Firewall: deny by default, no bypass, AKS subnet allowed. Your IP is added by hand
  # in Azure and Terraform never touches the IP list (see infra/README.md). Terraform itself creates the
  # container through Azure Resource Manager, which the firewall doesn't block.
  network_rules {
    default_action             = "Deny"
    virtual_network_subnet_ids = [azurerm_subnet.aks.id] # AKS nodes (see network.tf)
    bypass                     = ["None"] # no trusted-service exception; nothing needs it
  }

  # No versioning: it isn't supported with a hierarchical namespace.
  blob_properties {
    delete_retention_policy {
      days = var.soft_delete_days
    }
    container_delete_retention_policy {
      days = var.soft_delete_days
    }
  }

  tags = local.tags

  lifecycle {
    prevent_destroy = true                         # any plan that would destroy the lake fails
    ignore_changes  = [network_rules[0].ip_rules] # IPs are set by hand in Azure
  }
}

# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_container
resource "azurerm_storage_container" "lake" {
  name                  = "lake"
  storage_account_id    = azurerm_storage_account.lake.id # created via ARM, not the data plane
  container_access_type = "private"
}

# Read/write data in the lake as yourself (az login / DefaultAzureCredential / user-delegation SAS).
# Can take a few minutes to take effect after apply.
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/role_assignment
resource "azurerm_role_assignment" "lake_me" {
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Stops the account being deleted from the portal or CLI too. Doesn't affect the data inside.
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/management_lock
resource "azurerm_management_lock" "lake" {
  name       = "do-not-delete"
  scope      = azurerm_storage_account.lake.id
  lock_level = "CanNotDelete"
  notes      = "Lichess data lake"
}
