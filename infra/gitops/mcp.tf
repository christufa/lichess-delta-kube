# --- MCP server (#68): the Entra app MCP clients sign in to ---------------------------------
# The MCP server (mcp-server/, https://chess-mcp.<domain>/mcp) is an OAuth resource server for
# this app. A client (Claude, MCP Inspector, VS Code, the dashboard chat) signs the user in and
# gets a token for scope api://<app>/MCP.Access; the server accepts it only if the user is
# also *assigned* app role Lakehouse.Query (Entra needs the two values to differ). Assignment is required to sign in at all, and only
# the owner plus var.mcp_extra_users are assigned.
#
# Entra has no dynamic client registration, so clients use this app's client ID (and, for
# Claude's web connector, the client secret below; see the output mcp_client_secret).
locals {
  mcp_scope_id = "212729bb-1f9c-43de-b42c-d6246baf3e63" # oauth2 permission scope MCP.Access
  mcp_role_id  = "6dc3f75a-c8c2-4051-87a4-e8afe875d10b" # app role Lakehouse.Query
  azure_cli    = "04b07795-8ddb-461a-bbee-02f9e1bf7b46" # Microsoft's well-known Azure CLI client
}

resource "azuread_application" "mcp" {
  display_name     = "${var.hostname_prefix} MCP"
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azuread_client_config.current.object_id]

  api {
    requested_access_token_version = 2 # v2 tokens: iss = login.microsoftonline.com/<tenant>/v2.0, aud = client ID
    oauth2_permission_scope {
      id                         = local.mcp_scope_id
      value                      = "MCP.Access"
      type                       = "User"
      admin_consent_display_name = "Query the Lichess lakehouse"
      admin_consent_description  = "Run read-only queries on the Lichess lakehouse through its MCP server, as the signed-in user."
      user_consent_display_name  = "Query the Lichess lakehouse"
      user_consent_description   = "Run read-only queries on the Lichess lakehouse through its MCP server, as you."
      enabled                    = true
    }
  }

  app_role {
    id                   = local.mcp_role_id
    value                = "Lakehouse.Query"
    display_name         = "Lakehouse query"
    description          = "May use the lakehouse MCP server (read-only SQL over curated and gold)."
    allowed_member_types = ["User", "Application"] # Application: the chat backend's identity (#69)
    enabled              = true
  }

  # Mark app-only tokens (the chat backend's identity) with idtyp = "app". The MCP server tells them
  # from user tokens by it, and Entra only sends it when asked; user tokens never carry it.
  optional_claims {
    access_token {
      name = "idtyp"
    }
  }

  # Claude's custom connectors (web, with the client secret).
  web {
    redirect_uris = ["https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback"]
  }
  # Local clients with PKCE and no secret: MCP Inspector, VS Code.
  public_client {
    redirect_uris = [
      "http://localhost:6274/oauth/callback",
      "http://localhost:6274/oauth/callback/debug",
      "http://127.0.0.1:33418/",
      "https://vscode.dev/redirect",
    ]
  }

  # Set by azuread_application_identifier_uri.mcp (it needs the client ID first).
  lifecycle {
    ignore_changes = [identifier_uris]
  }
}

resource "azuread_application_identifier_uri" "mcp" {
  application_id = azuread_application.mcp.id
  identifier_uri = "api://${azuread_application.mcp.client_id}"
}

resource "azuread_service_principal" "mcp" {
  client_id                    = azuread_application.mcp.client_id
  owners                       = [data.azuread_client_config.current.object_id]
  app_role_assignment_required = true # nobody gets a token unless assigned
}

# Consent once for the whole tenant, so signing in doesn't ask each user to consent.
resource "azuread_service_principal_delegated_permission_grant" "mcp_self" {
  service_principal_object_id          = azuread_service_principal.mcp.object_id
  resource_service_principal_object_id = azuread_service_principal.mcp.object_id
  claim_values                         = ["MCP.Access"]
}

# The Azure CLI may ask for the scope without a consent prompt, so the owner can test with
#   az account get-access-token --scope api://<client id>/MCP.Access
resource "azuread_application_pre_authorized" "mcp_azure_cli" {
  application_id       = azuread_application.mcp.id
  authorized_client_id = local.azure_cli
  permission_ids       = [local.mcp_scope_id]
}

resource "azuread_app_role_assignment" "mcp_me" {
  app_role_id         = local.mcp_role_id
  principal_object_id = data.azuread_client_config.current.object_id
  resource_object_id  = azuread_service_principal.mcp.object_id
}

data "azuread_user" "mcp_extra" {
  for_each            = toset(var.mcp_extra_users)
  user_principal_name = each.value
}

resource "azuread_app_role_assignment" "mcp_extra" {
  for_each            = data.azuread_user.mcp_extra
  app_role_id         = local.mcp_role_id
  principal_object_id = each.value.object_id
  resource_object_id  = azuread_service_principal.mcp.object_id
}

# Client secret for Claude's custom connector: valid 180 days, replaced by the first apply after
# 150 days (then update it in Claude). Read it with: terraform output -raw mcp_client_secret
resource "time_rotating" "mcp_secret" {
  rotation_days = 150
}

resource "azuread_application_password" "mcp" {
  application_id      = azuread_application.mcp.id
  display_name        = "mcp-clients"
  end_date            = timeadd(time_rotating.mcp_secret.id, "4320h") # 180 days
  rotate_when_changed = { rotation = time_rotating.mcp_secret.id }
}
