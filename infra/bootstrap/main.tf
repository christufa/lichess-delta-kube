
# data means read, not create. A data source looks something up at plan time
# Terraform attempts to query data sources during the planning phase, but depending 
# on details in the configuration it may defer reading until the apply phase. 
# When Terraform must defer reading data sources, it indicates it in the plan output.
# Terraform may defer reading the data source to the apply phase when at least one 
# of the arguments refers to a resource attribute or other value that Terraform cannot predict 
# during the plan phase. 
# Syntax: one block per lookup; TYPE comes from a provider, NAME is your local label.
# data "<TYPE>" "<NAME>" {
#
#   # Arguments defined by the data source (see its Registry page).
#   [<ARGUMENT> = <EXPRESSION>]+
#   [<NESTED_BLOCK> {
#     <ARGUMENT> = <EXPRESSION>
#   }]+
#
#   # ---- Meta-arguments (Terraform-defined, work on every data source) ----
#
#   # Repeat the lookup: EITHER count OR for_each, never both.
#   [count    = <NUMBER>]                                # instances: data.<TYPE>.<NAME>[0]
#   |
#   [for_each = <MAP> | <SET_OF_STRINGS>]                # instances: data.<TYPE>.<NAME>["key"]
#
#   # Use a non-default provider configuration (an aliased one).
#   [provider = <PROVIDER>.<ALIAS>]                      # e.g. azurerm.shared
#
#   # Delay the read until these are applied (use sparingly; see note below).
#   [depends_on = [ <RESOURCE_OR_MODULE_ADDRESS>+ ]]
#
#   # Checks on the lookup.
#   [lifecycle {
#     [precondition {                                    # checked before reading
#       condition     = <BOOL_EXPRESSION>
#       error_message = "<MESSAGE>"
#     }]+
#     [postcondition {                                   # checked after reading; may use `self`
#       condition     = <BOOL_EXPRESSION>
#       error_message = "<MESSAGE>"
#     }]+
#   }]
# }
#
# Reference:  data.<TYPE>.<NAME>.<ATTRIBUTE>
#             data.<TYPE>.<NAME>[<INDEX>].<ATTRIBUTE>   (with count)
#             data.<TYPE>.<NAME>["<KEY>"].<ATTRIBUTE>   (with for_each)
# Add a data block to your configuration and specify the necessary arguments per the provider documentation. 
# You must include the type of data source and a label to use for the returned data. Refer to the provider documentation 
# for the types of data sources available. The following example declares an azurerm_client_config data source named current.
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/client_config
data "azurerm_client_config" "current" {}

# locals is exactly what is sounds/looks like. They are local variables to the module.
# Don't get caught up here. They are very simple, local c st ruct  
locals {
  tags = {
    project    = var.project
    component  = "tfstate"
    managed_by = "terraform"
  }
}

# Random string is used to generate a random suffix for the storage account name, 
# since it must be globally unique in Azure.
resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

# Simple, creates a resource group to hold the storage account and container for Terraform state.
# https://registry.terraform.io/providers/hashicorp/azurerm/1.42.0/docs/resources/resource_group
resource "azurerm_resource_group" "tfstate" {
  name     = "rg-${var.project}-tfstate"
  location = var.location
  tags     = local.tags
}

# Also simple, creates a storage account to hold the Terraform state file. The storage account name must be globally unique, 
# so we append a random string to the end of it. 
resource "azurerm_storage_account" "tfstate" {
  name                     = "st${var.project}tf${random_string.suffix.result}"
  resource_group_name      = azurerm_resource_group.tfstate.name
  location                 = azurerm_resource_group.tfstate.location
  account_tier             = "Standard"
  account_replication_type = "LRS"

  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false

  # Firewall: deny by default, no bypass. Your IP is added by hand in Azure and
  # Terraform never touches the IP list (see infra/README.md).
  # https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_account#network_rules
  network_rules {
    default_action = "Deny"
    bypass         = ["None"]
  }

  blob_properties {
    versioning_enabled = true
    delete_retention_policy {
      days = 15
    }
    container_delete_retention_policy {
      days = 15
    }
  }

  tags = local.tags

  lifecycle {
    ignore_changes = [network_rules[0].ip_rules] # IPs are set by hand in Azure
  }
}

# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_container.html
resource "azurerm_storage_container" "tfstate" {
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.tfstate.id
  container_access_type = "private"
}


# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/role_assignment
# Creates a role assignment for the current user to have access to the storage account. This is needed because the storage account 
# is created with no access keys, and the current user needs to be able to read/write the state file.
resource "azurerm_role_assignment" "me" {
  scope                = azurerm_storage_account.tfstate.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/management_lock
# Creates a management lock on the storage account to prevent accidental deletion. This is a "CanNotDelete" lock, which means that the 
# storage account cannot be deleted until the lock is removed. This is a good practice to prevent accidental deletion of the Terraform state file.
resource "azurerm_management_lock" "tfstate" {
  name       = "do-not-delete"
  scope      = azurerm_storage_account.tfstate.id
  lock_level = "CanNotDelete"
  notes      = "Holds Terraform state for all other infra"
}