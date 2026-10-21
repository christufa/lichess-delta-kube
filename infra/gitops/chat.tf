# --- Chat on the data (#69): the Entra app the dashboard signs users in to --------------------
# The dashboard (a single-page app at https://<prefix>.<domain>) signs the user in with MSAL (auth
# code + PKCE, no secret) and gets a token for scope api://<app>/Chat.Access. The chat backend
# accepts it only if the user is also *assigned* app role Chat.User: you plus var.mcp_extra_users.
# Everyone else can see the chat panel but not send.
#
# The backend itself reads the lakehouse through the MCP server as its own managed identity
# (infra/compute/chat.tf), which gets the MCP app's role Lakehouse.Query below.
locals {
  chat_scope_id = "7282c03e-5510-4cab-94da-451fd1d288e1" # oauth2 permission scope Chat.Access
  chat_role_id  = "e0ae1a3f-041d-463f-acc3-2ef3308ab628" # app role Chat.User
}

resource "azuread_application" "chat" {
  display_name     = "${var.hostname_prefix} Chat"
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azuread_client_config.current.object_id]

  api {
    requested_access_token_version = 2
    oauth2_permission_scope {
      id                         = local.chat_scope_id
      value                      = "Chat.Access"
      type                       = "User"
      admin_consent_display_name = "Chat about the Lichess lakehouse"
      admin_consent_description  = "Ask the Lichess lakehouse chat questions, as the signed-in user."
      user_consent_display_name  = "Chat about the Lichess lakehouse"
      user_consent_description   = "Ask the Lichess lakehouse chat questions, as you."
      enabled                    = true
    }
  }

  app_role {
    id                   = local.chat_role_id
    value                = "Chat.User" # must differ from the scope's value (the provider checks)
    display_name         = "Chat user"
    description          = "May send messages to the lakehouse chat (model spend is capped per day)."
    allowed_member_types = ["User"]
    enabled              = true
  }

  # The dashboard signs in from the browser: SPA platform, PKCE, no secret.
  single_page_application {
    redirect_uris = ["https://${local.public_hostname}/", "http://localhost:5173/"]
  }

  lifecycle {
    ignore_changes = [identifier_uris] # set by azuread_application_identifier_uri.chat
  }
}

resource "azuread_application_identifier_uri" "chat" {
  application_id = azuread_application.chat.id
  identifier_uri = "api://${azuread_application.chat.client_id}"
}

resource "azuread_service_principal" "chat" {
  client_id                    = azuread_application.chat.client_id
  owners                       = [data.azuread_client_config.current.object_id]
  app_role_assignment_required = true
}

# The SPA requests its own API's scope: consent once for the tenant.
resource "azuread_service_principal_delegated_permission_grant" "chat_self" {
  service_principal_object_id          = azuread_service_principal.chat.object_id
  resource_service_principal_object_id = azuread_service_principal.chat.object_id
  claim_values                         = ["Chat.Access"]
}

resource "azuread_app_role_assignment" "chat_me" {
  app_role_id         = local.chat_role_id
  principal_object_id = data.azuread_client_config.current.object_id
  resource_object_id  = azuread_service_principal.chat.object_id
}

resource "azuread_app_role_assignment" "chat_extra" {
  for_each            = data.azuread_user.mcp_extra # the same people who may use the MCP server
  app_role_id         = local.chat_role_id
  principal_object_id = each.value.object_id
  resource_object_id  = azuread_service_principal.chat.object_id
}

# The chat backend's managed identity may use the MCP server (app-only, role Lakehouse.Query).
resource "azuread_app_role_assignment" "chat_backend_mcp" {
  app_role_id         = local.mcp_role_id
  principal_object_id = local.compute.chat_identity_principal_id
  resource_object_id  = azuread_service_principal.mcp.object_id
}
