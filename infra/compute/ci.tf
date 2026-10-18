# --- CI identity (GitHub Actions OIDC) ---------------------------------------
# .github/workflows/images.yml signs in as this identity with a GitHub OIDC token and
# pushes images. Azure only accepts tokens for this repository's main branch, and the
# identity can do nothing but push to the registry. No stored secrets.
# https://learn.microsoft.com/azure/developer/github/connect-from-azure-openid-connect
resource "azurerm_user_assigned_identity" "ci" {
  name                = "id-${var.project}-ci"
  resource_group_name = azurerm_resource_group.compute.name
  location            = azurerm_resource_group.compute.location
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "ci_main" {
  name                      = "github-main"
  user_assigned_identity_id = azurerm_user_assigned_identity.ci.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = "https://token.actions.githubusercontent.com"
  subject                   = "repo:${var.github_repository}:ref:refs/heads/main"
}

resource "azurerm_role_assignment" "ci_acr" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.ci.principal_id
}

# --- Registry clean-up ----------------------------------------------------------
# Weekly ACR task (works on Basic): drop commit-SHA tags older than 30 days, keeping the
# newest 10 per image, then manifests left without tags. :main is never touched.
# https://learn.microsoft.com/azure/container-registry/container-registry-auto-purge
resource "azurerm_container_registry_task" "purge" {
  name                  = "purge-old-images"
  container_registry_id = azurerm_container_registry.main.id
  tags                  = local.tags

  platform {
    os = "Linux"
  }

  encoded_step {
    task_content = base64encode(<<-EOT
      version: v1.1.0
      steps:
        - cmd: acr purge --filter 'lichess-.*:^[0-9a-f]{40}$' --ago 30d --keep 10 --untagged
          disableWorkingDirectoryOverride: true
          timeout: 3600
    EOT
    )
  }

  timer_trigger {
    name     = "weekly"
    schedule = "0 7 * * 0" # Sundays 07:00 UTC
    enabled  = true
  }
}
