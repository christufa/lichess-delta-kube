# Monthly budget for this project only: costs in the Lichess resource groups,
# including the node resource group AKS creates for its VMs. Anything else in the
# subscription (e.g. Databricks) isn't counted. Alerts only: Azure budgets never
# stop or delete anything. The hard limits are the spot pool's max_count (compute)
# and your vCPU quotas.
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/consumption_budget_subscription
locals {
  budget_resource_groups = [
    "rg-${var.project}-tfstate",
    "rg-${var.project}-platform",
    "rg-${var.project}-compute",
    "rg-${var.project}-compute-nodes", # created by AKS; holds the VMs, disks, load balancer
  ]
}

resource "azurerm_consumption_budget_subscription" "monthly" {
  name            = "budget-${var.project}-monthly"
  subscription_id = "/subscriptions/${var.subscription_id}"
  amount          = var.budget_amount
  time_grain      = "Monthly"

  time_period {
    start_date = var.budget_start_date
  }

  filter {
    dimension {
      name   = "ResourceGroupName"
      values = local.budget_resource_groups
    }
  }

  # Spend so far this month
  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_emails = var.budget_emails
  }
  notification {
    enabled        = true
    threshold      = 80
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_emails = var.budget_emails
  }
  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_emails = var.budget_emails
  }

  # Early warning: Azure projects you'll go over by month end
  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Forecasted"
    contact_emails = var.budget_emails
  }
}
