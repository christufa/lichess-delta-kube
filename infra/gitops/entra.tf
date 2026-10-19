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

# --- Admins group: who gets admin in the apps that support group-based roles -------------
resource "azuread_group" "admins" {
  display_name     = "${var.hostname_prefix} admins"
  description      = "Admins of the ${var.hostname_prefix} platform (Argo CD role:admin)"
  security_enabled = true
  owners           = [data.azuread_client_config.current.object_id]
  members          = [data.azuread_client_config.current.object_id]
}

# --- Argo CD: Entra sign-in with workload identity (no client secret) --------------------
# https://argo-cd.readthedocs.io/en/stable/operator-manual/user-management/microsoft/
resource "azuread_application" "argocd" {
  display_name            = "${var.hostname_prefix} Argo CD"
  sign_in_audience        = "AzureADMyOrg"
  owners                  = [data.azuread_client_config.current.object_id]
  group_membership_claims = ["SecurityGroup"] # group object IDs in the token, for RBAC

  web {
    redirect_uris = ["https://${local.private_hosts.cd.hostname}/auth/callback"]
  }
  public_client {
    redirect_uris = ["http://localhost:8085/auth/callback"] # argocd login --sso
  }

  required_resource_access {
    resource_app_id = data.azuread_application_published_app_ids.well_known.result["MicrosoftGraph"]
    resource_access {
      id   = data.azuread_service_principal.msgraph.oauth2_permission_scope_ids["User.Read"]
      type = "Scope"
    }
  }
}

resource "azuread_service_principal" "argocd" {
  client_id                    = azuread_application.argocd.client_id
  owners                       = [data.azuread_client_config.current.object_id]
  app_role_assignment_required = true
}

# Users are assigned directly: assigning groups to apps needs Entra ID P1.
resource "azuread_app_role_assignment" "argocd_me" {
  app_role_id         = "00000000-0000-0000-0000-000000000000"
  principal_object_id = data.azuread_client_config.current.object_id
  resource_object_id  = azuread_service_principal.argocd.object_id
}

# argocd-server's Kubernetes service account may act as this app registration.
resource "azuread_application_federated_identity_credential" "argocd_server" {
  application_id = azuread_application.argocd.id
  display_name   = "aks-argocd-argocd-server"
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = data.azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject        = "system:serviceaccount:argocd:argocd-server"
}

