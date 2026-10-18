# Entra ID app registration that Cloudflare Access signs users in with (#45).
# Only users assigned to the app can sign in at all (app_role_assignment_required), and
# only you are assigned; Access policies (edge.tf) then check the email on top.
# https://developers.cloudflare.com/cloudflare-one/identity/idp-integration/entra-id/
data "azuread_client_config" "current" {}

data "azuread_application_published_app_ids" "well_known" {}

data "azuread_service_principal" "msgraph" {
  client_id = data.azuread_application_published_app_ids.well_known.result["MicrosoftGraph"]
}

locals {
  cloudflare_access_scopes = ["openid", "email", "profile", "offline_access", "User.Read"]
}

resource "azuread_application" "cloudflare_access" {
  display_name     = "${var.hostname_prefix} Cloudflare Access"
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azuread_client_config.current.object_id]

  web {
    redirect_uris = ["https://${var.cloudflare_team_name}.cloudflareaccess.com/cdn-cgi/access/callback"]
  }

  required_resource_access {
    resource_app_id = data.azuread_application_published_app_ids.well_known.result["MicrosoftGraph"]
    dynamic "resource_access" {
      for_each = local.cloudflare_access_scopes
      content {
        id   = data.azuread_service_principal.msgraph.oauth2_permission_scope_ids[resource_access.value]
        type = "Scope"
      }
    }
  }
}

resource "azuread_service_principal" "cloudflare_access" {
  client_id                    = azuread_application.cloudflare_access.client_id
  owners                       = [data.azuread_client_config.current.object_id]
  app_role_assignment_required = true # nobody signs in unless assigned below
}

resource "azuread_app_role_assignment" "cloudflare_access_me" {
  app_role_id         = "00000000-0000-0000-0000-000000000000" # default access
  principal_object_id = data.azuread_client_config.current.object_id
  resource_object_id  = azuread_service_principal.cloudflare_access.object_id
}

# Client secret for Cloudflare: valid 180 days, replaced by the first apply after 150 days. It goes straight into the Cloudflare identity provider (and Terraform
# state); it is never written to a file or to the cluster.
resource "time_rotating" "cloudflare_access_secret" {
  rotation_days = 150
}

resource "azuread_application_password" "cloudflare_access" {
  application_id      = azuread_application.cloudflare_access.id
  display_name        = "cloudflare-access"
  end_date            = timeadd(time_rotating.cloudflare_access_secret.id, "4320h") # 180 days
  rotate_when_changed = { rotation = time_rotating.cloudflare_access_secret.id }
}
