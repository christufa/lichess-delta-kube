# --- Cost reporting (#31) ------------------------------------------------------------------
# The jobs identity may read (only) the costs of this project's resource groups, the same ones
# the budget counts (infra/bootstrap/budget.tf). spark/jobs/build_azure_cost.py queries the
# Cost Management API with it daily and writes lake.gold.azure_cost: no export, no storage
# keys, no firewall exception for Cost Management.
locals {
  cost_resource_groups = [
    "rg-${var.project}-tfstate",
    "rg-${var.project}-platform",
    azurerm_resource_group.compute.name,
    azurerm_kubernetes_cluster.main.node_resource_group, # AKS creates it; holds the VMs and disks
  ]
}

# Scopes built from the names, not read with a data source: a data source that depends on the
# cluster is deferred whenever the cluster changes, which would force these to be replaced.
resource "azurerm_role_assignment" "jobs_cost" {
  for_each             = toset(local.cost_resource_groups)
  scope                = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${each.value}"
  role_definition_name = "Cost Management Reader"
  principal_id         = azurerm_user_assigned_identity.jobs.principal_id
}
